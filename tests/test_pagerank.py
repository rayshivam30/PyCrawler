"""
Unit tests for the PageRankCalculator.

Uses the sync in-memory compute_sync() method so no database is required.
"""

import unittest
from app.ranking.pagerank import PageRankCalculator, DAMPING


class TestPageRank(unittest.TestCase):

    def test_single_page_no_links(self):
        """A single page with no links should get a PageRank of 1.0 (normalized)."""
        scores = PageRankCalculator.compute_sync([1], [])
        self.assertIn(1, scores)
        self.assertAlmostEqual(scores[1], 1.0, places=4)

    def test_empty_graph(self):
        """Empty page list should return empty dict without crashing."""
        scores = PageRankCalculator.compute_sync([], [])
        self.assertEqual(scores, {})

    def test_two_pages_one_link(self):
        """
        Page A links to Page B.
        Page B receives rank from A, so B should have higher final score than A.
        """
        scores = PageRankCalculator.compute_sync([1, 2], [(1, 2)])
        # B (id=2) is pointed to, A (id=1) has no inlinks
        self.assertGreater(scores[2], scores[1])

    def test_scores_normalized_to_one(self):
        """Maximum score in any graph should be normalized to 1.0."""
        pages = [1, 2, 3, 4, 5]
        edges = [(1, 2), (2, 3), (3, 4), (4, 5), (5, 1)]  # ring graph
        scores = PageRankCalculator.compute_sync(pages, edges)
        max_score = max(scores.values())
        self.assertAlmostEqual(max_score, 1.0, places=4)

    def test_all_scores_positive(self):
        """All PageRank scores must be positive (base teleportation ensures this)."""
        pages = [1, 2, 3]
        edges = [(1, 2), (1, 3)]  # page 1 links out, 2 and 3 are dangling
        scores = PageRankCalculator.compute_sync(pages, edges)
        for pid, score in scores.items():
            self.assertGreater(score, 0.0, f"Page {pid} has non-positive score {score}")

    def test_hub_page_ranks_higher(self):
        """
        A hub page that many others link to should rank higher than
        a page that no one links to.
        """
        pages = [1, 2, 3, 4, 5]
        # Pages 1-4 all link to page 5; page 5 links to nobody
        edges = [(1, 5), (2, 5), (3, 5), (4, 5)]
        scores = PageRankCalculator.compute_sync(pages, edges)
        # Page 5 is the hub — it should outrank all others
        self.assertGreater(scores[5], scores[1])
        self.assertGreater(scores[5], scores[2])
        self.assertGreater(scores[5], scores[3])
        self.assertGreater(scores[5], scores[4])

    def test_self_links_ignored(self):
        """Self-links are filtered out in the graph construction (same src/dst)."""
        pages = [1, 2]
        edges = [(1, 1), (2, 2), (1, 2)]  # self-links + 1->2
        scores = PageRankCalculator.compute_sync(pages, edges)
        # Page 2 receives rank from page 1; page 1 receives nothing useful
        # Scores should still be positive and sane
        self.assertIn(1, scores)
        self.assertIn(2, scores)
        for s in scores.values():
            self.assertGreater(s, 0.0)

    def test_damping_factor_effect(self):
        """
        In a two-page graph where A→B, B should approach a steady state
        influenced by the damping factor.  Rough sanity: B score > (1-d)/N.
        """
        pages = [1, 2]
        edges = [(1, 2)]
        scores = PageRankCalculator.compute_sync(pages, edges)
        base = (1.0 - DAMPING) / len(pages)
        # B should clearly exceed the base teleportation share
        self.assertGreater(scores[2], base / max(scores.values()))

    def test_symmetric_ring_equal_scores(self):
        """A perfect ring A→B→C→A should yield equal PageRank for all nodes."""
        pages = [1, 2, 3]
        edges = [(1, 2), (2, 3), (3, 1)]
        scores = PageRankCalculator.compute_sync(pages, edges)
        s1, s2, s3 = scores[1], scores[2], scores[3]
        self.assertAlmostEqual(s1, s2, places=3)
        self.assertAlmostEqual(s2, s3, places=3)

    def test_convergence_large_graph(self):
        """Larger graph (50 pages, chain) should converge without error."""
        pages = list(range(1, 51))
        edges = [(i, i + 1) for i in range(1, 50)]  # linear chain
        scores = PageRankCalculator.compute_sync(pages, edges)
        self.assertEqual(len(scores), 50)
        self.assertAlmostEqual(max(scores.values()), 1.0, places=4)


if __name__ == "__main__":
    unittest.main()
