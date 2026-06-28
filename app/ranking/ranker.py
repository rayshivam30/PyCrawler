"""
Ranker — Blended search ranking: TF-IDF + PageRank + Semantic Similarity.

Scoring formula:
    When semantic search available:
        final = 0.5 × tfidf  +  0.3 × pagerank  +  0.2 × semantic

    When semantic search unavailable (sentence-transformers not installed):
        final = 0.7 × tfidf  +  0.3 × pagerank

Algorithm:
    1. Tokenize query → stem → remove stop words.
    2. Retrieve TF scores from InvertedIndex for matching words.
    3. Compute IDF per word across the corpus.
    4. Aggregate TF-IDF score per page (top-50 candidate pool).
    5. Normalize TF-IDF scores within the candidate set → [0, 1].
    6. Look up PageRank score from Page.pagerank (already normalized [0,1]).
    7. If semantic available: encode query, load candidate embeddings,
       batch cosine similarity → [0, 1].
    8. Blend all signals using fixed weights.
    9. Re-sort by final score, return top 20.
"""

import math
import re
import logging
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List, Dict, Any, Optional

from app.models.models import Page, Word, InvertedIndex, PageEmbedding
from app.search.tokenizer import Tokenizer
from app.search.embeddings import EmbeddingEngine

logger = logging.getLogger(__name__)

# Scoring weights — must sum to 1.0
W_TFIDF_FULL   = 0.5   # when all three signals are active
W_PAGERANK     = 0.3
W_SEMANTIC     = 0.2

W_TFIDF_NO_SEM = 0.7   # when no semantic search (only TF-IDF + PageRank)

# Candidate pool size before final re-ranking
CANDIDATE_POOL = 50


class Ranker:

    @staticmethod
    def generate_snippet(content: str, query_tokens: List[str], max_len: int = 200) -> str:
        """
        Generate a contextual text snippet centred around the first matched token.
        Falls back to the beginning of the content if no token is found.
        """
        if not content:
            return ""
        if not query_tokens:
            return content[:max_len] + ("..." if len(content) > max_len else "")

        content_lower = content.lower()
        first_idx = -1
        for token in query_tokens:
            match = re.search(r'\b' + re.escape(token), content_lower)
            if match:
                idx = match.start()
                if first_idx == -1 or idx < first_idx:
                    first_idx = idx

        if first_idx == -1:
            return content[:max_len] + ("..." if len(content) > max_len else "")

        start = max(0, first_idx - 60)
        end = min(len(content), first_idx + max_len - 60)
        snippet = content[start:end]

        if start > 0:
            space_idx = snippet.find(" ")
            snippet = ("..." + snippet[space_idx + 1:]) if space_idx != -1 else ("..." + snippet)
        if end < len(content):
            rspace_idx = snippet.rfind(" ")
            snippet = (snippet[:rspace_idx] + "...") if rspace_idx != -1 else (snippet + "...")

        return snippet

    @staticmethod
    async def search(db: AsyncSession, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Execute blended TF-IDF + PageRank + Semantic search.

        Returns a list of result dicts sorted by final blended score.
        Each result includes a score_breakdown field showing signal contributions.
        """
        query_tokens = Tokenizer.tokenize(query)
        if not query_tokens:
            return []

        # ── Signal: TF-IDF ────────────────────────────────────────────────
        n_stmt = select(func.count(Page.id))
        total_pages = (await db.execute(n_stmt)).scalar() or 0
        if total_pages == 0:
            return []

        # Fetch raw TF and word for every matching index entry
        idx_stmt = (
            select(InvertedIndex.page_id, InvertedIndex.tf, Word.word)
            .join(Word, InvertedIndex.word_id == Word.id)
            .where(Word.word.in_(query_tokens))
        )
        rows = (await db.execute(idx_stmt)).all()

        if not rows:
            return []

        # Group by word to compute Document Frequency
        word_matches: Dict[str, List[Dict]] = {}
        for page_id, tf, word in rows:
            word_matches.setdefault(word, []).append({"page_id": page_id, "tf": tf})

        # IDF per query token
        idf_map: Dict[str, float] = {
            word: math.log(1.0 + total_pages / (1.0 + len(matches)))
            for word, matches in word_matches.items()
        }

        # Aggregate TF-IDF scores
        raw_tfidf: Dict[int, float] = {}
        for word, matches in word_matches.items():
            idf = idf_map[word]
            for m in matches:
                pid = m["page_id"]
                raw_tfidf[pid] = raw_tfidf.get(pid, 0.0) + m["tf"] * idf

        if not raw_tfidf:
            return []

        # Select top-CANDIDATE_POOL by TF-IDF for re-ranking
        top_candidates = sorted(raw_tfidf.items(), key=lambda x: x[1], reverse=True)[:CANDIDATE_POOL]
        candidate_ids = [pid for pid, _ in top_candidates]

        # Normalize TF-IDF within the candidate set → [0, 1]
        max_tfidf = top_candidates[0][1] if top_candidates else 1.0
        norm_tfidf: Dict[int, float] = {
            pid: score / max_tfidf for pid, score in top_candidates
        }

        # ── Fetch full page records for candidates ────────────────────────
        pages_result = await db.execute(select(Page).where(Page.id.in_(candidate_ids)))
        pages: List[Page] = pages_result.scalars().all()
        page_map: Dict[int, Page] = {p.id: p for p in pages}

        # ── Signal: PageRank ──────────────────────────────────────────────
        # Page.pagerank is already normalized [0,1] by PageRankCalculator.
        # Pages that have never had PageRank computed use default 0.15.
        max_pr = max((page_map[pid].pagerank for pid in candidate_ids if pid in page_map), default=1.0)
        if max_pr == 0:
            max_pr = 1.0
        norm_pr: Dict[int, float] = {
            pid: (page_map[pid].pagerank / max_pr) if pid in page_map else 0.0
            for pid in candidate_ids
        }

        # ── Signal: Semantic Similarity ───────────────────────────────────
        semantic_available = EmbeddingEngine.is_available()
        query_embedding: Optional[List[float]] = None
        norm_semantic: Dict[int, float] = {pid: 0.0 for pid in candidate_ids}

        if semantic_available:
            query_embedding = await asyncio.to_thread(EmbeddingEngine.encode, query)
            if query_embedding:
                # Load stored embeddings for the candidate pages (one batch query)
                emb_rows = (await db.execute(
                    select(PageEmbedding.page_id, PageEmbedding.embedding)
                    .where(PageEmbedding.page_id.in_(candidate_ids))
                )).all()

                if emb_rows:
                    emb_page_ids = [row.page_id for row in emb_rows]
                    emb_vectors = [
                        EmbeddingEngine.json_to_embedding(row.embedding) or []
                        for row in emb_rows
                    ]

                    # Batch cosine similarity (numpy-accelerated)
                    similarities = EmbeddingEngine.batch_cosine_similarity(
                        query_embedding, emb_vectors
                    )

                    # Normalize similarities to [0, 1] — cosine is in [-1, 1]
                    max_sim = max(similarities) if similarities else 1.0
                    if max_sim <= 0:
                        max_sim = 1.0

                    for pid, sim in zip(emb_page_ids, similarities):
                        norm_semantic[pid] = max(0.0, sim / max_sim)

        # ── Blend signals ──────────────────────────────────────────────────
        if semantic_available and query_embedding:
            w_tfidf, w_pr, w_sem = W_TFIDF_FULL, W_PAGERANK, W_SEMANTIC
        else:
            w_tfidf, w_pr, w_sem = W_TFIDF_NO_SEM, W_PAGERANK, 0.0

        results: List[Dict[str, Any]] = []
        for page in pages:
            pid = page.id
            t = norm_tfidf.get(pid, 0.0)
            p = norm_pr.get(pid, 0.0)
            s = norm_semantic.get(pid, 0.0)
            final = w_tfidf * t + w_pr * p + w_sem * s

            snippet = Ranker.generate_snippet(page.content or "", query_tokens)
            results.append({
                "id": pid,
                "url": page.url,
                "title": page.title or "Untitled",
                "snippet": snippet,
                "score": round(final, 5),
                "language": page.language,
                "crawl_time": page.crawl_time.isoformat() if page.crawl_time else None,
                "score_breakdown": {
                    "tfidf": round(t, 4),
                    "pagerank": round(p, 4),
                    "semantic": round(s, 4),
                },
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]
