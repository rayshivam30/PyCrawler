import os
import random
import shutil

# Technical vocabulary list for keyword indexing and semantic search
TECH_WORDS = [
    "python", "asyncio", "redis", "postgres", "fastapi", "docker", "compose", "sql", "database",
    "indexing", "concurrency", "benchmark", "performance", "search", "pagerank", "algorithm",
    "vector", "embedding", "model", "inference", "latency", "throughput", "cache", "server",
    "client", "request", "response", "cluster", "distributed", "worker", "thread", "coroutine",
    "event", "loop", "schema", "inverted", "index", "crawler", "robots", "delay", "politeness",
    "parse", "beautifulsoup", "lxml", "html", "css", "javascript", "network", "socket", "port"
]

SENTENCE_TEMPLATES = [
    "The {word1} library provides high-performance {word2} operations.",
    "Using {word1} with {word2} reduces overall search latency.",
    "This {word1} model generates semantic vector {word2} structures.",
    "Our distributed {word1} worker pool coordinates via {word2}.",
    "We can benchmark {word1} throughput to optimize {word2} usage.",
    "The {word1} inverted index maps tokens to their {word2} occurrences.",
    "Enforcing {word1} politeness prevents {word2} server crashes.",
    "PageRank evaluates the significance of {word1} based on {word2} inbound edges."
]

def generate_sentence():
    template = random.choice(SENTENCE_TEMPLATES)
    w1 = random.choice(TECH_WORDS)
    w2 = random.choice(TECH_WORDS)
    while w2 == w1:
        w2 = random.choice(TECH_WORDS)
    return template.format(word1=w1, word2=w2)

def generate_mock_corpus(output_dir="mock_corpus", num_pages=2000):
    print(f"Generating {num_pages} synthetic HTML pages in '{output_dir}'...")
    
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir)

    # Generate page filenames
    filenames = [f"page_{i}.html" for i in range(num_pages)]

    for i in range(num_pages):
        filename = filenames[i]
        
        # Select 5 to 20 random pages to link to
        num_links = random.randint(5, 20)
        linked_pages = random.sample(filenames, num_links)
        # Ensure page_0 links to several pages and there are no self links
        if filename in linked_pages:
            linked_pages.remove(filename)

        # Generate paragraphs
        paragraphs = []
        for _ in range(random.randint(3, 8)):
            sentences = " ".join(generate_sentence() for _ in range(random.randint(4, 10)))
            paragraphs.append(f"<p>{sentences}</p>")

        # Inject outgoing links into paragraphs
        links_html = "".join(f'<li><a href="{link}">Link to {link[:-5].replace("_", " ").title()}</a></li>' for link in linked_pages)

        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Mock Web Corpus - Page {i}</title>
    <meta name="description" content="A synthetic test page containing technical content about {random.choice(TECH_WORDS)} and {random.choice(TECH_WORDS)}.">
    <meta name="keywords" content="{random.choice(TECH_WORDS)}, {random.choice(TECH_WORDS)}, test, corpus">
</head>
<body>
    <h1>Synthetic Technical Document - Page {i}</h1>
    <hr>
    <div>
        {"".join(paragraphs)}
    </div>
    <hr>
    <h3>Outgoing Hyperlinks:</h3>
    <ul>
        {links_html}
    </ul>
</body>
</html>
"""
        with open(os.path.join(output_dir, filename), "w", encoding="utf-8") as f:
            f.write(html_content)

    # Write a simple robots.txt
    with open(os.path.join(output_dir, "robots.txt"), "w") as f:
        f.write("User-agent: *\nDisallow:\nCrawl-delay: 0\n")

    print(f"[SUCCESS] Generated {num_pages} mock pages in '{output_dir}' successfully!")

if __name__ == "__main__":
    generate_mock_corpus(num_pages=2000)
