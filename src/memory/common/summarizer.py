import logging
import traceback
from typing import Any

from bs4 import BeautifulSoup

from memory.common import settings, chunker

logger = logging.getLogger(__name__)

MAX_TOKENS = 200000
TAGS_PROMPT = """
The following text is already concise. Please identify 3-5 relevant tags that capture the main topics or themes.

Tags should be lowercase and use hyphens instead of spaces, e.g. "machine-learning" instead of "Machine Learning".

Return your response as XML with this format:
<summary>{summary}</summary>
<tags>
    <tag>tag1</tag>
    <tag>tag2</tag>
    <tag>tag3</tag>
</tags>

Text:
{content}
"""

SUMMARY_PROMPT = """
Please summarize the following text into approximately {target_tokens} tokens ({target_chars} characters).
Also provide 3-5 relevant tags that capture the main topics or themes.

Tags should be lowercase and use hyphens instead of spaces, e.g. "machine-learning" instead of "Machine Learning".

Return your response as XML with this format:

<summary>your summary here</summary>
<tags>
    <tag>tag1</tag>
    <tag>tag2</tag>
    <tag>tag3</tag>
</tags>

Text to summarize:
{content}
"""


def parse_response(response: str) -> dict[str, Any]:
    """Parse the response from the summarizer."""
    if not response or not response.strip():
        return {"summary": "", "tags": []}

    # Use html.parser instead of xml parser for better compatibility
    soup = BeautifulSoup(response, "html.parser")

    # Safely extract summary
    summary_element = soup.find("summary")
    summary = summary_element.text if summary_element else ""

    # Safely extract tags
    tag_elements = soup.find_all("tag")
    tags = [tag.text for tag in tag_elements if tag.text is not None]

    return {"summary": summary, "tags": tags}


def _call_openai(prompt: str) -> dict[str, Any]:
    """Call OpenAI API for summarization."""
    import openai

    client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
    try:
        response = client.chat.completions.create(
            model=settings.SUMMARIZER_MODEL.split("/")[1],
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant that creates concise summaries and identifies key topics.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=2048,
        )
        return parse_response(response.choices[0].message.content or "")
    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        raise


def _call_anthropic(prompt: str) -> dict[str, Any]:
    """Call Anthropic API for summarization."""
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        response = client.messages.create(
            model=settings.SUMMARIZER_MODEL.split("/")[1],
            messages=[{"role": "user", "content": prompt}],
            system="You are a helpful assistant that creates concise summaries and identifies key topics. Always respond with valid XML.",
            temperature=0.3,
            max_tokens=2048,
        )
        return parse_response(response.content[0].text)
    except Exception as e:
        logger.error(f"Anthropic API error: {e}")
        logger.error(response.content[0].text)
        raise


def truncate(content: str, target_tokens: int) -> str:
    target_chars = target_tokens * chunker.CHARS_PER_TOKEN
    if len(content) > target_chars:
        return content[:target_chars].rsplit(" ", 1)[0] + "..."
    return content


def summarize(content: str, target_tokens: int | None = None) -> tuple[str, list[str]]:
    """
    Summarize content to approximately target_tokens length and generate tags.

    Args:
        content: Text to summarize
        target_tokens: Target length in tokens (defaults to DEFAULT_CHUNK_TOKENS)

    Returns:
        Tuple of (summary, tags)
    """
    if not content or not content.strip():
        return "", []

    if target_tokens is None:
        target_tokens = settings.DEFAULT_CHUNK_TOKENS

    summary, tags = content, []

    # If content is already short enough, just extract tags
    current_tokens = chunker.approx_token_count(content)
    if current_tokens <= target_tokens:
        logger.info(
            f"Content already under {target_tokens} tokens, extracting tags only"
        )
        prompt = TAGS_PROMPT.format(content=content, summary=summary[:1000])
    else:
        prompt = SUMMARY_PROMPT.format(
            target_tokens=target_tokens,
            target_chars=target_tokens * chunker.CHARS_PER_TOKEN,
            content=content,
        )

    if chunker.approx_token_count(prompt) > MAX_TOKENS:
        logger.warning(
            f"Prompt too long ({chunker.approx_token_count(prompt)} tokens), truncating"
        )
        prompt = truncate(prompt, MAX_TOKENS - 20)

    try:
        if settings.SUMMARIZER_MODEL.startswith("anthropic"):
            result = _call_anthropic(prompt)
        else:
            result = _call_openai(prompt)

        summary = result.get("summary", "")
        tags = result.get("tags", [])
    except Exception as e:
        traceback.print_exc()
        logger.error(f"Summarization failed: {e}")

    tokens = chunker.approx_token_count(summary)
    if tokens > target_tokens * 1.5:
        logger.warning(f"Summary too long ({tokens} tokens), truncating")
        summary = truncate(content, target_tokens)

    return summary, tags
