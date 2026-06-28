import re
from typing import List, Set

# Standard English stop words list
STOP_WORDS: Set[str] = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "aren't",
    "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "by",
    "can't", "cannot", "could", "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't",
    "down", "during", "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't", "have",
    "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here", "here's", "hers", "herself",
    "him", "himself", "his", "how", "how's", "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into",
    "is", "isn't", "it", "it's", "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself",
    "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought", "our", "ours",
    "ourselves", "out", "over", "own", "same", "shan't", "she", "she'd", "she'll", "she's", "should",
    "shouldn't", "so", "some", "such", "than", "that", "that's", "the", "their", "theirs", "them",
    "themselves", "then", "there", "there's", "these", "they", "they'd", "they'll", "they're", "they've",
    "this", "those", "through", "to", "too", "under", "until", "up", "very", "was", "wasn't", "we",
    "we'd", "we'll", "we're", "we've", "were", "weren't", "what", "what's", "when", "when's", "where",
    "where's", "which", "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
    "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours", "yourself", "yourselves"
}

class Tokenizer:
    @staticmethod
    def stem(word: str) -> str:
        """
        Lightweight suffix stemmer for English.
        Reduces basic inflectional endings like plurals, past tense, and continuous verbs.
        """
        w = word.lower().strip()
        if len(w) <= 3:
            return w

        # Remove basic plurals
        if w.endswith("sses"):
            w = w[:-2]
        elif w.endswith("ies"):
            w = w[:-3] + "i"
        elif w.endswith("es") and not w.endswith("ees"):
            w = w[:-2]
        elif w.endswith("ss"):
            pass
        elif w.endswith("s") and not (w.endswith("us") or w.endswith("is") or w.endswith("as")):
            w = w[:-1]

        # Suffix handling
        if w.endswith("eed"):
            w = w[:-1]  # agreed -> agree
        elif w.endswith("ing"):
            w = w[:-3]
            # Handle double consonants (e.g. running -> runn -> run)
            if len(w) > 2 and w[-1] == w[-2] and w[-1] not in ('l', 's', 'z'):
                w = w[:-1]
            elif w.endswith("at") or w.endswith("bl") or w.endswith("iz"):
                w += "e"
        elif w.endswith("ed"):
            w = w[:-2]
            # Handle double consonants
            if len(w) > 2 and w[-1] == w[-2] and w[-1] not in ('l', 's', 'z'):
                w = w[:-1]
            elif w.endswith("at") or w.endswith("bl") or w.endswith("iz"):
                w += "e"
        elif w.endswith("er") and len(w) > 4:
            w = w[:-2]
            # Handle double consonants
            if len(w) > 2 and w[-1] == w[-2] and w[-1] not in ('l', 's', 'z'):
                w = w[:-1]
        elif w.endswith("ly"):
            w = w[:-2]
        elif w.endswith("ment") and len(w) > 6:
            w = w[:-4]

        return w

    @staticmethod
    def tokenize(text: str) -> List[str]:
        """
        Tokenizes text by converting to lowercase, removing punctuation,
        filtering stop words, and applying suffix stemming.
        """
        if not text:
            return []

        # Find word blocks (words, numbers, words with hyphens)
        words = re.findall(r'\b[a-zA-Z0-9\-]+\b', text.lower())

        tokens = []
        for word in words:
            # Skip stop words, short elements, and excessively long tokens (like base64 data)
            if word in STOP_WORDS or len(word) < 2 or len(word) > 50:
                continue

            stemmed = Tokenizer.stem(word)
            if stemmed and stemmed not in STOP_WORDS:
                tokens.append(stemmed)

        return tokens
