"""
PageRankCalculator — Iterative damped PageRank algorithm.

Formula:
    PR(A) = (1 - d) / N  +  d × Σ [ PR(T) / |out(T)| ]

Where:
    d  = damping factor (0.85) — probability of following a link vs. teleporting
    N  = total number of pages
    T  = pages that link to page A
    |out(T)| = number of outlinks from page T

Dangling nodes (pages with no outlinks) have their rank redistributed evenly
across all pages — this prevents rank from "leaking" out of the graph.

Convergence is checked with L1 norm; the algorithm stops early when
Σ|PR_new(i) - PR_old(i)| < CONVERGENCE_THRESHOLD.
"""

import logging
import math
from collections import defaultdict
from typing import Dict, List, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Page, Link

logger = logging.getLogger(__name__)

DAMPING = 0.85
MAX_ITERATIONS = 50
CONVERGENCE_THRESHOLD = 1e-6


class PageRankCalculator:

    @staticmethod
    async def compute(db: AsyncSession) -> Dict:
        """
        Run the full PageRank computation over all crawled pages.

        Steps:
          1. Load all page IDs from DB.
          2. Load all Link edges from DB.
          3. Iterate PageRank formula until convergence or MAX_ITERATIONS.
          4. Normalize scores into [0, 1] range.
          5. Bulk-update Page.pagerank in the database.

        Returns a dict of stats for the API response.
        """
        # ── Step 1: Load all page IDs ──────────────────────────────────────
        page_rows = (await db.execute(select(Page.id))).scalars().all()
        if not page_rows:
            logger.warning("PageRank: No pages found in database.")
            return {"pages_computed": 0, "iterations": 0, "converged": False}

        N = len(page_rows)
        page_ids = list(page_rows)
        logger.info(f"PageRank: Computing over {N} pages.")

        # ── Step 2: Load link graph ────────────────────────────────────────
        link_rows = (await db.execute(
            select(Link.source_page, Link.destination_page)
        )).all()

        # Build adjacency structures: O(L) time
        outlinks: Dict[int, List[int]] = defaultdict(list)   # src -> [dst, ...]
        inlinks: Dict[int, List[int]] = defaultdict(list)    # dst -> [src, ...]

        for src, dst in link_rows:
            if src in set(page_ids) and dst in set(page_ids):
                outlinks[src].append(dst)
                inlinks[dst].append(src)

        L = sum(len(v) for v in outlinks.values())
        logger.info(f"PageRank: Link graph loaded — {L} edges.")

        # ── Step 3: Iterative PageRank ─────────────────────────────────────
        base_rank = (1.0 - DAMPING) / N
        scores: Dict[int, float] = {pid: 1.0 / N for pid in page_ids}
        page_id_set = set(page_ids)

        dangling_nodes = {pid for pid in page_ids if len(outlinks.get(pid, [])) == 0}

        converged = False
        iterations_run = 0

        for iteration in range(MAX_ITERATIONS):
            iterations_run += 1

            # Dangling node contribution: ranks drain into a "teleport" pool
            dangling_sum = sum(scores[pid] for pid in dangling_nodes)
            dangling_contrib = DAMPING * dangling_sum / N

            new_scores: Dict[int, float] = {}
            for pid in page_ids:
                # Teleportation base + dangling redistribution
                rank = base_rank + dangling_contrib

                # Link-based contributions from pages pointing to this page
                for src in inlinks.get(pid, []):
                    out_count = len(outlinks[src])
                    if out_count > 0:
                        rank += DAMPING * scores[src] / out_count

                new_scores[pid] = rank

            # Check L1 convergence
            delta = sum(abs(new_scores[pid] - scores[pid]) for pid in page_ids)
            scores = new_scores

            if delta < CONVERGENCE_THRESHOLD:
                converged = True
                logger.info(f"PageRank converged after {iterations_run} iterations (Δ={delta:.2e})")
                break

        if not converged:
            logger.info(f"PageRank stopped at max {MAX_ITERATIONS} iterations.")

        # ── Step 4: Normalize scores to [0, 1] ────────────────────────────
        max_score = max(scores.values()) if scores else 1.0
        if max_score > 0:
            normalized: Dict[int, float] = {pid: v / max_score for pid, v in scores.items()}
        else:
            normalized = scores

        # ── Step 5: Bulk update Page.pagerank ─────────────────────────────
        for pid, pr in normalized.items():
            await db.execute(
                update(Page).where(Page.id == pid).values(pagerank=round(pr, 8))
            )
        await db.commit()

        top5 = sorted(normalized.items(), key=lambda x: x[1], reverse=True)[:5]
        logger.info(f"PageRank complete. Top 5 page IDs by score: {top5}")

        return {
            "pages_computed": N,
            "link_edges": L,
            "iterations": iterations_run,
            "converged": converged,
            "max_raw_score": round(max_score, 8),
        }

    @staticmethod
    def compute_sync(page_ids: List[int], edges: List[Tuple[int, int]]) -> Dict[int, float]:
        """
        Pure in-memory PageRank computation (no DB).
        Used for unit testing without a database connection.

        Args:
            page_ids: list of page IDs in the graph
            edges: list of (source_id, destination_id) tuples

        Returns:
            Dict mapping page_id -> normalized PageRank score [0, 1]
        """
        N = len(page_ids)
        if N == 0:
            return {}

        outlinks: Dict[int, List[int]] = defaultdict(list)
        inlinks: Dict[int, List[int]] = defaultdict(list)
        page_id_set = set(page_ids)

        for src, dst in edges:
            if src in page_id_set and dst in page_id_set:
                outlinks[src].append(dst)
                inlinks[dst].append(src)

        base_rank = (1.0 - DAMPING) / N
        scores: Dict[int, float] = {pid: 1.0 / N for pid in page_ids}
        dangling_nodes = {pid for pid in page_ids if len(outlinks.get(pid, [])) == 0}

        for _ in range(MAX_ITERATIONS):
            dangling_sum = sum(scores[pid] for pid in dangling_nodes)
            dangling_contrib = DAMPING * dangling_sum / N

            new_scores: Dict[int, float] = {}
            for pid in page_ids:
                rank = base_rank + dangling_contrib
                for src in inlinks.get(pid, []):
                    out_count = len(outlinks[src])
                    if out_count > 0:
                        rank += DAMPING * scores[src] / out_count
                new_scores[pid] = rank

            delta = sum(abs(new_scores[p] - scores[p]) for p in page_ids)
            scores = new_scores
            if delta < CONVERGENCE_THRESHOLD:
                break

        max_score = max(scores.values()) if scores else 1.0
        return {pid: v / max_score for pid, v in scores.items()} if max_score > 0 else scores
