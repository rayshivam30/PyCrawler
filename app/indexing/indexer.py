"""
Indexer — TF-IDF keyword indexing + semantic embedding generation.

After indexing a page's keywords (word frequency → inverted index), the indexer
also generates a semantic embedding via EmbeddingEngine and stores it in the
page_embeddings table. If sentence-transformers is unavailable, the embedding
step is silently skipped and only TF-IDF indexing runs.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.models.models import Word, InvertedIndex, PageEmbedding
from app.search.tokenizer import Tokenizer
from app.search.embeddings import EmbeddingEngine
from collections import Counter
import logging

logger = logging.getLogger(__name__)

class Indexer:
    @staticmethod
    async def index_page(db: AsyncSession, page_id: int, content: str) -> None:
        """
        Tokenizes page content, calculates Term Frequency, and inserts
        records into Word and InvertedIndex tables. Safely clears existing
        index entries for the page to prevent duplicate key conflicts.

        Also generates and stores a semantic embedding if EmbeddingEngine
        is available (sentence-transformers installed).
        """
        if not content:
            return

        tokens = Tokenizer.tokenize(content)
        if not tokens:
            return

        total_tokens = len(tokens)
        counts = Counter(tokens)

        # Clear existing index entries for this page to support re-indexing/updates safely
        delete_stmt = delete(InvertedIndex).where(InvertedIndex.page_id == page_id)
        await db.execute(delete_stmt)

        unique_tokens = list(counts.keys())
        word_id_map = {}

        # Fetch existing words from the database
        stmt = select(Word).where(Word.word.in_(unique_tokens))
        result = await db.execute(stmt)
        existing_words = result.scalars().all()
        for w in existing_words:
            word_id_map[w.word] = w.id

        # Insert new words using thread-safe Postgres ON CONFLICT DO NOTHING
        new_words = [w for w in unique_tokens if w not in word_id_map]
        if new_words:
            insert_stmt = pg_insert(Word).values([{"word": w} for w in new_words])
            insert_stmt = insert_stmt.on_conflict_do_nothing(index_elements=["word"])
            await db.execute(insert_stmt)
            await db.flush()

            # Retrieve the word records to fill out the map
            stmt = select(Word).where(Word.word.in_(new_words))
            result = await db.execute(stmt)
            added_words = result.scalars().all()
            for w in added_words:
                word_id_map[w.word] = w.id

        # Write Term Frequency (TF) mappings to the InvertedIndex table
        for token, count in counts.items():
            word_id = word_id_map.get(token)
            if word_id:
                tf = count / total_tokens
                index_entry = InvertedIndex(
                    word_id=word_id,
                    page_id=page_id,
                    tf=tf
                )
                db.add(index_entry)

        await db.flush()
        logger.info(f"Indexed page {page_id}: {total_tokens} tokens, {len(unique_tokens)} unique.")

        # ── Semantic Embedding (optional) ──────────────────────────────────
        import asyncio
        embedding_vec = await asyncio.to_thread(EmbeddingEngine.encode, content)
        if embedding_vec is not None:
            emb_json = EmbeddingEngine.embedding_to_json(embedding_vec)
            model_name = "all-MiniLM-L6-v2"

            # Delete existing embedding for this page (upsert pattern)
            await db.execute(delete(PageEmbedding).where(PageEmbedding.page_id == page_id))

            db.add(PageEmbedding(
                page_id=page_id,
                embedding=emb_json,
                model_name=model_name,
            ))
            await db.flush()
            logger.debug(f"Stored semantic embedding for page {page_id}.")

