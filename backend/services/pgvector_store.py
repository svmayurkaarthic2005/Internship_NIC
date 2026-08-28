"""
pgvector_store.py — Vector Store backed by PostgreSQL + pgvector
================================================================
Vector database for semantic search and RAG.

Tamil Nadu Revenue Department — Sub Inspector Surveyor AI Assistant

All vector operations run inside the existing PostgreSQL ``sis_chatbot_db``
database using the ``pgvector`` extension.

Embedding model : nomic-embed-text via Ollama  (768 dimensions)
Index type      : HNSW cosine similarity  (vector_cosine_ops)
Sync transport  : psycopg2  (the RAG pipeline is synchronous)

Public API
----------
  init_pgvector()                                 — verify extension + table
  add_documents(docs)                             — bulk upsert with embeddings
  similarity_search(query, n_results, where_filter) — cosine ANN search
  delete_collection()                             — TRUNCATE knowledge_embeddings
  get_collection_stats()                          — row count + status dict
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector

from backend.config import settings
from backend.services.embeddings import batch_embed, generate_embedding
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Embedding dimension for nomic-embed-text
# ---------------------------------------------------------------------------
EMBED_DIM = 768


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def _get_sync_conn() -> psycopg2.extensions.connection:
    """
    Open a synchronous psycopg2 connection to the configured database.

    The DSN comes from SYNC_DATABASE_URL on every platform. It used to be
    hardcoded to sis_chatbot on Windows, which meant the RAG store silently
    stayed on one database while the rest of the app followed .env -- document
    answers then came from a different database than the record answers.
    Any '@' in the password must be %-encoded in .env for this to parse.
    """
    raw_url: str = settings.SYNC_DATABASE_URL
    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql://") \
                      .replace("postgresql+psycopg2://", "postgresql://")
    conn = psycopg2.connect(sync_url)
    # Register pgvector codec so numpy arrays round-trip correctly
    register_vector(conn)
    return conn


# ---------------------------------------------------------------------------
# init_pgvector
# ---------------------------------------------------------------------------

def init_pgvector() -> None:
    """
    Verify that the pgvector extension is enabled and the
    ``knowledge_embeddings`` table exists.

    Called once at application startup (from main.py lifespan).
    Raises if the extension is missing — the operator must run:
        CREATE EXTENSION IF NOT EXISTS vector;
    in the ``sis_chatbot_db`` database first.
    """
    conn = _get_sync_conn()
    try:
        with conn.cursor() as cur:
            # Check extension
            cur.execute(
                "SELECT 1 FROM pg_extension WHERE extname = 'vector';"
            )
            if not cur.fetchone():
                raise RuntimeError(
                    "pgvector extension is not installed in sis_chatbot_db. "
                    "Run: CREATE EXTENSION IF NOT EXISTS vector;"
                )

            # Ensure table exists (idempotent — SQLAlchemy create_all may
            # have already run, but we guard here too)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_embeddings (
                    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                    chunk_id    TEXT        UNIQUE NOT NULL,
                    content     TEXT        NOT NULL,
                    embedding   vector(768) NOT NULL,
                    source      TEXT,
                    category    TEXT,
                    section     TEXT,
                    language    TEXT DEFAULT 'en',
                    page        INTEGER DEFAULT 0,
                    created_at  TIMESTAMPTZ DEFAULT now(),
                    updated_at  TIMESTAMPTZ DEFAULT now()
                );
                """
            )
            conn.commit()
            logger.info("pgvector store: extension verified, table ready")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# add_documents
# ---------------------------------------------------------------------------

def add_documents(docs: List[Dict[str, Any]]) -> None:
    """
    Embed and upsert document chunks into ``knowledge_embeddings``.

    Accepts the following dict shape per document:

    .. code-block:: python

        {
            "id":       str,          # unique chunk_id
            "content":  str,          # text to embed  (preferred key)
            "text":     str,          # alias for "content" (also accepted)
            "metadata": {
                "document_name": str,
                "source":        str,
                "category":      str,
                "section":       str,
                "language":      str,   # 'english' / 'tamil' / 'tanglish'
                "page_number":   int,
            }
        }

    Language values are normalised:
        ``"english"``  → ``"en"``
        ``"tamil"``    → ``"ta"``
        everything else passes through unchanged.

    Embeddings are generated in one batch call to Ollama, then each row is
    upserted individually inside a transaction.
    """
    if not docs:
        logger.warning("add_documents called with empty list")
        return

    # ── 1. Extract text content ──────────────────────────────────────────
    contents: List[str] = []
    for doc in docs:
        text = doc.get("content") or doc.get("text") or ""
        contents.append(text.strip())

    # ── 2. Generate embeddings in one batch call ─────────────────────────
    logger.info(f"Generating embeddings for {len(contents)} chunks …")
    try:
        embeddings = batch_embed(contents)
    except Exception as exc:
        logger.error(f"Batch embedding failed: {exc}")
        raise

    # ── 3. Language normalisation map ───────────────────────────────────
    _lang_map = {"english": "en", "tamil": "ta"}

    # ── 4. Upsert each chunk ─────────────────────────────────────────────
    conn = _get_sync_conn()
    try:
        with conn.cursor() as cur:
            for doc, content, embedding in zip(docs, contents, embeddings):
                if not content:
                    logger.warning(
                        f"Skipping doc '{doc.get('id')}': empty content"
                    )
                    continue

                chunk_id = doc.get("id") or doc.get("chunk_id") or ""
                if not chunk_id:
                    logger.warning("Skipping doc with no 'id' field")
                    continue

                meta = doc.get("metadata") or {}
                raw_lang = meta.get("language", "en")
                language = _lang_map.get(raw_lang, raw_lang)

                cur.execute(
                    """
                    INSERT INTO knowledge_embeddings
                        (id, chunk_id, content, embedding,
                         source, category, section, language, page, created_at, updated_at)
                    VALUES
                        (gen_random_uuid(), %(chunk_id)s, %(content)s, %(embedding)s,
                         %(source)s,   %(category)s, %(section)s,
                         %(language)s, %(page)s, now(), now())
                    ON CONFLICT (chunk_id) DO UPDATE SET
                        content   = EXCLUDED.content,
                        embedding = EXCLUDED.embedding,
                        source    = EXCLUDED.source,
                        category  = EXCLUDED.category,
                        section   = EXCLUDED.section,
                        language  = EXCLUDED.language,
                        page      = EXCLUDED.page,
                        updated_at = now()
                    """,
                    {
                        "chunk_id":  chunk_id,
                        "content":   content,
                        "embedding": embedding,
                        "source":    meta.get("source"),
                        "category":  meta.get("category"),
                        "section":   meta.get("section"),
                        "language":  language,
                        "page":      meta.get("page_number") or meta.get("page") or 0,
                    },
                )
        conn.commit()
        logger.info(f"Successfully upserted {len(docs)} chunks into pgvector")
    except Exception as exc:
        conn.rollback()
        logger.error(f"add_documents DB error: {exc}")
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# similarity_search
# ---------------------------------------------------------------------------

def similarity_search(
    query: str,
    n_results: int = 5,
    where_filter: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Returns a list of dicts:

    .. code-block:: python

        [
            {
                "content":  str,
                "metadata": {
                    "document_name": str,
                    "category":      str,
                    "source":        str,
                    "language":      str,
                    "page_number":   int,
                },
                "distance": float,   # cosine distance (lower = more similar)
            },
            …
        ]

    ``where_filter`` format:
        ``{"language": "english"}``   →  ``WHERE language = 'en'``
        ``{"language": "tamil"}``     →  ``WHERE language = 'ta'``
        ``{"category": "workflow"}``  →  ``WHERE category = 'workflow'``

    Tanglish / no-filter searches omit the WHERE clause entirely.
    """
    if not query or not query.strip():
        logger.warning("similarity_search called with empty query")
        return []

    # ── 1. Embed the query ───────────────────────────────────────────────
    try:
        query_vec = generate_embedding(query.strip())
    except Exception as exc:
        logger.error(f"Failed to embed query: {exc}")
        return []

    # ── 2. Language normalisation (full words → ISO codes) ──────
    _lang_map = {"english": "en", "tamil": "ta"}

    params: Dict[str, Any] = {"vec": str(query_vec), "n": n_results}
    where_clauses: List[str] = []

    if where_filter:
        for col, val in where_filter.items():
            if col in ("language", "category", "source", "section"):
                normalised = _lang_map.get(str(val), str(val))
                param_key = f"filter_{col}"
                where_clauses.append(f"{col} = %({param_key})s")
                params[param_key] = normalised

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    sql = f"""
        SELECT
            content,
            source,
            category,
            section,
            language,
            page,
            embedding <=> %(vec)s::vector  AS distance
        FROM knowledge_embeddings
        {where_sql}
        ORDER BY embedding <=> %(vec)s::vector
        LIMIT %(n)s;
    """

    # ── 4. Execute ───────────────────────────────────────────────────────
    conn = _get_sync_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    except Exception as exc:
        logger.error(f"similarity_search query error: {exc}")
        return []
    finally:
        conn.close()

    # ── 5. Format results ───────────────────────────────────────────────────────
    results: List[Dict[str, Any]] = []
    for row in rows:
        results.append(
            {
                "content": row["content"],
                "metadata": {
                    "document_name": row["source"] or "Unknown",
                    "source":        row["source"],
                    "category":      row["category"],
                    "section":       row["section"],
                    "language":      row["language"],
                    "page_number":   row["page"],
                },
                "distance": float(row["distance"]),
            }
        )

    logger.info(
        f"similarity_search returned {len(results)} results "
        f"(filter={where_filter})"
    )
    return results


# ---------------------------------------------------------------------------
# delete_collection
# ---------------------------------------------------------------------------

def delete_collection() -> None:
    """
    Remove all rows from ``knowledge_embeddings``.
    Use with caution — data is not recoverable.
    """
    conn = _get_sync_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE knowledge_embeddings;")
        conn.commit()
        logger.warning("knowledge_embeddings table truncated (all vectors deleted)")
    except Exception as exc:
        conn.rollback()
        logger.error(f"delete_collection error: {exc}")
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# get_collection_stats
# ---------------------------------------------------------------------------

def get_collection_stats() -> Dict[str, Any]:
    """
    Return document count and status.

    .. code-block:: python

        {
            "collection_name":  "knowledge_embeddings",
            "document_count":   1234,
            "status":           "initialized",   # or "empty" / "error"
        }
    """
    conn = _get_sync_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM knowledge_embeddings;")
            count: int = cur.fetchone()[0]
        return {
            "collection_name": "knowledge_embeddings",
            "document_count":  count,
            "status":          "initialized" if count > 0 else "empty",
        }
    except Exception as exc:
        logger.error(f"get_collection_stats error: {exc}")
        return {
            "collection_name": "knowledge_embeddings",
            "document_count":  0,
            "status":          "error",
            "error":           str(exc),
        }
    finally:
        conn.close()
