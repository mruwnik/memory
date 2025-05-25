import hashlib
import logging
from typing import Iterable, cast

from memory.common import chunker, embedding, qdrant
from memory.common.db.connection import make_session
from memory.common.db.models import BlogPost
from memory.common.parsers.blogs import parse_webpage
from memory.workers.celery_app import app

logger = logging.getLogger(__name__)


SYNC_WEBPAGE = "memory.workers.tasks.blogs.sync_webpage"


def create_blog_post_from_article(article, tags: Iterable[str] = []) -> BlogPost:
    """Create a BlogPost model from parsed article data."""
    return BlogPost(
        url=article.url,
        title=article.title,
        published=article.published_date,
        content=article.content,
        sha256=hashlib.sha256(article.content.encode()).digest(),
        modality="blog",
        tags=tags,
        mime_type="text/markdown",
        size=len(article.content.encode("utf-8")),
    )


def embed_blog_post(blog_post: BlogPost) -> int:
    """Embed blog post content and return count of successfully embedded chunks."""
    try:
        # Always embed the full content
        _, chunks = embedding.embed(
            "text/markdown",
            cast(str, blog_post.content),
            metadata=blog_post.as_payload(),
            chunk_size=chunker.EMBEDDING_MAX_TOKENS,
        )
        # But also embed the content in chunks (unless it's really short)
        if (
            chunker.approx_token_count(cast(str, blog_post.content))
            > chunker.DEFAULT_CHUNK_TOKENS * 2
        ):
            _, small_chunks = embedding.embed(
                "text/markdown",
                cast(str, blog_post.content),
                metadata=blog_post.as_payload(),
            )
            chunks += small_chunks

        if chunks:
            blog_post.chunks = chunks
            blog_post.embed_status = "QUEUED"  # type: ignore
            return len(chunks)
        else:
            blog_post.embed_status = "FAILED"  # type: ignore
            logger.warning(f"No chunks generated for blog post: {blog_post.title}")
            return 0

    except Exception as e:
        blog_post.embed_status = "FAILED"  # type: ignore
        logger.error(f"Failed to embed blog post {blog_post.title}: {e}")
        return 0


def push_to_qdrant(blog_post: BlogPost):
    """Push embeddings to Qdrant for successfully embedded blog post."""
    if cast(str, blog_post.embed_status) != "QUEUED" or not blog_post.chunks:
        return

    try:
        vector_ids = [str(chunk.id) for chunk in blog_post.chunks]
        vectors = [chunk.vector for chunk in blog_post.chunks]
        payloads = [chunk.item_metadata for chunk in blog_post.chunks]

        qdrant.upsert_vectors(
            client=qdrant.get_qdrant_client(),
            collection_name="blog",
            ids=vector_ids,
            vectors=vectors,
            payloads=payloads,
        )

        blog_post.embed_status = "STORED"  # type: ignore
        logger.info(f"Successfully stored embeddings for: {blog_post.title}")

    except Exception as e:
        blog_post.embed_status = "FAILED"  # type: ignore
        logger.error(f"Failed to push embeddings to Qdrant: {e}")
        raise


@app.task(name=SYNC_WEBPAGE)
def sync_webpage(url: str, tags: Iterable[str] = []) -> dict:
    """
    Synchronize a webpage from a URL.

    Args:
        url: URL of the webpage to parse and store
        tags: Additional tags to apply to the content

    Returns:
        dict: Summary of what was processed
    """
    article = parse_webpage(url)

    if not article.content:
        logger.warning(f"Article content too short or empty: {url}")
        return {
            "url": url,
            "title": article.title,
            "status": "skipped_short_content",
            "content_length": 0,
        }

    blog_post = create_blog_post_from_article(article, tags)

    with make_session() as session:
        existing_post = session.query(BlogPost).filter(BlogPost.url == url).first()
        if existing_post:
            logger.info(f"Blog post already exists: {existing_post.title}")
            return {
                "blog_post_id": existing_post.id,
                "url": url,
                "title": existing_post.title,
                "status": "already_exists",
                "chunks_count": len(existing_post.chunks),
            }

        existing_post = (
            session.query(BlogPost).filter(BlogPost.sha256 == blog_post.sha256).first()
        )
        if existing_post:
            logger.info(
                f"Blog post with the same content already exists: {existing_post.title}"
            )
            return {
                "blog_post_id": existing_post.id,
                "url": url,
                "title": existing_post.title,
                "status": "already_exists",
                "chunks_count": len(existing_post.chunks),
            }

        session.add(blog_post)
        session.flush()

        chunks_count = embed_blog_post(blog_post)
        session.flush()

        try:
            push_to_qdrant(blog_post)
            logger.info(
                f"Successfully processed webpage: {blog_post.title} "
                f"({chunks_count} chunks embedded)"
            )
        except Exception as e:
            logger.error(f"Failed to push embeddings to Qdrant: {e}")
            blog_post.embed_status = "FAILED"  # type: ignore

        session.commit()

        return {
            "blog_post_id": blog_post.id,
            "url": url,
            "title": blog_post.title,
            "author": article.author,
            "published_date": article.published_date,
            "status": "processed",
            "chunks_count": chunks_count,
            "content_length": len(article.content),
            "embed_status": blog_post.embed_status,
        }
