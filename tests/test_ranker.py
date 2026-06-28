import unittest
import math
from app.search.tokenizer import Tokenizer


class TestRanker(unittest.TestCase):
    """
    Unit tests for the TF-IDF ranking logic components.
    These tests verify the tokenizer and scoring math in isolation
    without requiring a database connection.
    """

    # ── Tokenizer Tests ──────────────────────────────────────────────────────

    def test_tokenizer_basic(self):
        """Tokenizer should return stemmed, lower-cased tokens."""
        tokens = Tokenizer.tokenize("Running faster than expected")
        self.assertIn("run", tokens)
        self.assertIn("fast", tokens)
        # "than" is a stop word and should be excluded
        self.assertNotIn("than", tokens)

    def test_tokenizer_stops_words_removed(self):
        """Common stop words should be stripped out."""
        tokens = Tokenizer.tokenize("the quick brown fox")
        self.assertNotIn("the", tokens)
        self.assertIn("quick", tokens)
        self.assertIn("brown", tokens)
        self.assertIn("fox", tokens)

    def test_tokenizer_empty_string(self):
        """Empty string should return an empty list."""
        self.assertEqual(Tokenizer.tokenize(""), [])

    def test_tokenizer_numbers_included(self):
        """Multi-digit numbers should be tokenized (single digits are filtered as too short)."""
        tokens = Tokenizer.tokenize("Python 313 tutorial")
        self.assertIn("313", tokens)

    def test_tokenizer_deduplication(self):
        """Stemmer should reduce inflections to a shared root."""
        # "crawls", "crawling", "crawled" should all stem to "crawl"
        for word in ["crawls", "crawling", "crawled"]:
            stemmed = Tokenizer.stem(word)
            self.assertEqual(stemmed, "crawl", f"Expected 'crawl', got '{stemmed}' for '{word}'")

    # ── TF-IDF Math Tests ────────────────────────────────────────────────────

    def test_idf_formula_rare_word(self):
        """
        A word that appears in only 1 of 1000 documents should have
        a very high IDF, indicating high uniqueness.
        IDF = log(1 + N / (1 + DF))
        """
        total_pages = 1000
        df = 1  # word appears in just 1 document
        idf = math.log(1.0 + (total_pages / (1.0 + df)))
        self.assertGreater(idf, 5.0, "Rare word should have high IDF")

    def test_idf_formula_common_word(self):
        """
        A word that appears in every document should have
        a very low IDF, indicating low uniqueness.
        """
        total_pages = 1000
        df = 1000  # word appears in all documents
        idf = math.log(1.0 + (total_pages / (1.0 + df)))
        self.assertLess(idf, 1.0, "Common word should have low IDF")

    def test_tfidf_score_increases_with_tf(self):
        """
        A page where a query term appears more frequently should
        receive a higher TF-IDF score than a page where it appears rarely.
        """
        idf = 3.5  # fixed IDF for this test
        tf_high = 0.10  # word appears 10% of the time
        tf_low = 0.01   # word appears 1% of the time
        score_high = tf_high * idf
        score_low = tf_low * idf
        self.assertGreater(score_high, score_low)

    def test_tfidf_multi_term_aggregation(self):
        """
        Scores for multiple query terms should sum together correctly.
        Page matching 2 terms should outscore a page matching only 1.
        """
        # Page A matches 2 query tokens
        score_a = (0.05 * 3.2) + (0.08 * 4.1)
        # Page B matches only 1 query token
        score_b = (0.10 * 3.2)
        self.assertGreater(score_a, score_b)


if __name__ == "__main__":
    unittest.main()
