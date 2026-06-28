import unittest
from app.parser.parser import HTMLParser

class TestHTMLParser(unittest.TestCase):
    def setUp(self):
        self.html = """
        <html lang="en">
        <head>
            <title>PyCrawler Test Page</title>
            <meta name="description" content="A simple test page for crawler parser verification.">
            <meta name="keywords" content="test, crawler, python, beautifulsoup">
        </head>
        <body>
            <h1>Main Title</h1>
            <h2>Sub Title</h2>
            <p>Python is an amazing programming language. Web crawling is fun!</p>
            <a href="/page1">Relative Page 1</a>
            <a href="https://docs.python.org/3/library/">Absolute Python Docs</a>
            <img src="/assets/logo.png" alt="Logo">
        </body>
        </html>
        """
        self.base_url = "https://example.com"
        self.parsed = HTMLParser.parse(self.html, self.base_url)

    def test_meta_extraction(self):
        self.assertEqual(self.parsed["title"], "PyCrawler Test Page")
        self.assertEqual(self.parsed["description"], "A simple test page for crawler parser verification.")
        self.assertEqual(self.parsed["language"], "en")
        self.assertIn("test", self.parsed["keywords"])
        self.assertIn("crawler", self.parsed["keywords"])

    def test_content_extraction(self):
        content = self.parsed["content"]
        self.assertIn("Python is an amazing programming language", content)
        self.assertIn("Web crawling is fun", content)
        self.assertNotIn("<script>", content)
        # Verify page hash is generated
        self.assertTrue(len(self.parsed["page_hash"]) > 0)

    def test_links_and_images_resolution(self):
        links = self.parsed["links"]
        # Relative link /page1 resolves to https://example.com/page1
        self.assertIn("https://example.com/page1", links)
        self.assertIn("https://docs.python.org/3/library/", links)
        
        images = self.parsed["images"]
        self.assertIn("https://example.com/assets/logo.png", images)

    def test_headings_extraction(self):
        headings = self.parsed["headings"]
        self.assertIn("Main Title", headings)
        self.assertIn("Sub Title", headings)

if __name__ == '__main__':
    unittest.main()
