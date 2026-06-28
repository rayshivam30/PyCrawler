import unittest
from app.search.tokenizer import Tokenizer

class TestTokenizer(unittest.TestCase):
    def test_basic_tokenization(self):
        text = "Python is awesome and we are building a distributed web crawler!"
        tokens = Tokenizer.tokenize(text)
        # Verify that stop words (is, and, we, are, a) are removed
        self.assertNotIn("is", tokens)
        self.assertNotIn("and", tokens)
        self.assertNotIn("we", tokens)
        self.assertNotIn("are", tokens)
        self.assertNotIn("a", tokens)
        
        # Verify that remaining tokens are lowercase
        self.assertIn("python", tokens)
        self.assertIn("build", tokens)  # building -> build (stemmed)
        self.assertIn("distribut", tokens)  # distributed -> distribut (stemmed)
        self.assertIn("crawl", tokens)  # crawler -> crawl (stemmed)

    def test_suffix_stemming(self):
        self.assertEqual(Tokenizer.stem("running"), "run")
        self.assertEqual(Tokenizer.stem("agreed"), "agree")
        self.assertEqual(Tokenizer.stem("agreement"), "agree")
        self.assertEqual(Tokenizer.stem("pythonic"), "pythonic") # short/no change
        self.assertEqual(Tokenizer.stem("cats"), "cat")
        self.assertEqual(Tokenizer.stem("indexes"), "index")

    def test_punctuation_and_whitespace(self):
        text = "Hello, world!!! This -- is a... test-case."
        tokens = Tokenizer.tokenize(text)
        self.assertIn("hello", tokens)
        self.assertIn("world", tokens)
        self.assertIn("test-case", tokens)

if __name__ == '__main__':
    unittest.main()
