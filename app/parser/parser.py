from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import hashlib
import re
from typing import Dict, Any, List, Set

class HTMLParser:
    @staticmethod
    def clean_text(text: str) -> str:
        """Strip consecutive whitespaces and trim the content."""
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    @staticmethod
    def parse(html_content: str, base_url: str) -> Dict[str, Any]:
        """
        Parses raw HTML to extract structured metadata, clean body text,
        images, outgoing hyperlinks, and headings. Computes a text hash
        for deduplication.
        """
        # Use lxml parser for ultra-fast speed, fallback to html.parser if not installed
        try:
            soup = BeautifulSoup(html_content, 'lxml')
        except Exception:
            soup = BeautifulSoup(html_content, 'html.parser')

        # 1. Extract title
        title = soup.title.string if soup.title else ""
        if title:
            title = title.strip()

        # 2. Extract Meta Description
        meta_desc = ""
        desc_tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        if desc_tag and desc_tag.get("content"):
            meta_desc = desc_tag.get("content").strip()

        # 3. Extract Meta Keywords
        meta_keywords = []
        keywords_tag = soup.find("meta", attrs={"name": re.compile(r"^keywords$", re.I)})
        if keywords_tag and keywords_tag.get("content"):
            meta_keywords = [k.strip() for k in keywords_tag.get("content").split(",") if k.strip()]

        # 4. Extract Language configuration
        lang = ""
        html_tag = soup.find("html")
        if html_tag and html_tag.get("lang"):
            lang = html_tag.get("lang")[:10]  # Grab short language identifier

        # 5. Extract and resolve absolute Outgoing Links
        links: Set[str] = set()
        for a_tag in soup.find_all("a", href=True):
            href = a_tag.get("href")
            absolute_url = urljoin(base_url, href)
            parsed_abs = urlparse(absolute_url)
            if parsed_abs.scheme in ("http", "https"):
                # Filter out non-HTML file extensions
                path = parsed_abs.path.lower()
                if any(path.endswith(ext) for ext in [
                    ".pdf", ".zip", ".tar.gz", ".tgz", ".rar", ".gz",
                    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico",
                    ".css", ".js", ".json", ".xml", ".rss", ".mp4", ".mp3", ".avi",
                    ".wmv", ".mov", ".xlsx", ".docx", ".pptx", ".csv", ".txt", ".exe"
                ]):
                    continue

                cleaned_url = absolute_url.split('#')[0]
                if cleaned_url:
                    links.add(cleaned_url)

        # 6. Extract Headings (h1, h2, h3)
        headings: List[str] = []
        for h in soup.find_all(["h1", "h2", "h3"]):
            h_text = HTMLParser.clean_text(h.get_text())
            if h_text:
                headings.append(h_text)

        # 7. Extract Image URLs
        images: Set[str] = set()
        for img in soup.find_all("img", src=True):
            src = img.get("src")
            abs_src = urljoin(base_url, src)
            if urlparse(abs_src).scheme in ("http", "https"):
                images.add(abs_src)

        # 8. Decompose script, style, and navigation tags before converting to body text
        for tag in soup(["script", "style", "meta", "noscript", "header", "footer", "nav"]):
            tag.decompose()

        raw_text = soup.get_text(separator=' ')
        cleaned_text = HTMLParser.clean_text(raw_text)

        # SHA-256 hash for duplicate content body detection
        page_hash = hashlib.sha256(cleaned_text.encode('utf-8')).hexdigest()

        return {
            "title": title or "Untitled",
            "content": cleaned_text,
            "page_hash": page_hash,
            "description": meta_desc,
            "keywords": meta_keywords,
            "language": lang or "en",
            "links": list(links),
            "headings": headings,
            "images": list(images)
        }
