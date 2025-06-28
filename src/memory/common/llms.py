import logging
import base64
import io
from typing import Any
from PIL import Image

from memory.common import settings, tokens

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are a helpful assistant that creates concise summaries and identifies key topics.
"""


def encode_image(image: Image.Image) -> str:
    """Encode PIL Image to base64 string."""
    buffer = io.BytesIO()
    # Convert to RGB if necessary (for RGBA, etc.)
    if image.mode != "RGB":
        image = image.convert("RGB")
    image.save(buffer, format="JPEG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def call_openai(
    prompt: str,
    model: str,
    images: list[Image.Image] = [],
    system_prompt: str = SYSTEM_PROMPT,
) -> str:
    """Call OpenAI API for summarization."""
    import openai

    client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
    try:
        user_content: Any = [{"type": "text", "text": prompt}]
        if images:
            for image in images:
                encoded_image = encode_image(image)
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"},
                    }
                )

        response = client.chat.completions.create(
            model=model.split("/")[1],
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=2048,
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        raise


def call_anthropic(
    prompt: str,
    model: str,
    images: list[Image.Image] = [],
    system_prompt: str = SYSTEM_PROMPT,
) -> str:
    """Call Anthropic API for summarization."""
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        # Prepare the message content
        content: Any = [{"type": "text", "text": prompt}]
        if images:
            # Add images if provided
            for image in images:
                encoded_image = encode_image(image)
                content.append(
                    {  # type: ignore
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": encoded_image,
                        },
                    }
                )

        response = client.messages.create(
            model=model.split("/")[1],
            messages=[{"role": "user", "content": content}],  # type: ignore
            system=system_prompt,
            temperature=0.3,
            max_tokens=2048,
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Anthropic API error: {e}")
        raise


def call(
    prompt: str,
    model: str,
    images: list[Image.Image] = [],
    system_prompt: str = SYSTEM_PROMPT,
) -> str:
    if model.startswith("anthropic"):
        return call_anthropic(prompt, model, images, system_prompt)
    return call_openai(prompt, model, images, system_prompt)


def truncate(content: str, target_tokens: int) -> str:
    target_chars = target_tokens * tokens.CHARS_PER_TOKEN
    if len(content) > target_chars:
        return content[:target_chars].rsplit(" ", 1)[0] + "..."
    return content
