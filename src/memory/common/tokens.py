import logging
from PIL import Image
import math

logger = logging.getLogger(__name__)


CHARS_PER_TOKEN = 4


def approx_token_count(s: str) -> int:
    return len(s) // CHARS_PER_TOKEN


def estimate_openai_image_tokens(image: Image.Image, detail: str = "high") -> int:
    """
    Estimate tokens for an image using OpenAI's counting method.

    Args:
        image: PIL Image
        detail: "high" or "low" detail level

    Returns:
        Estimated token count
    """
    if detail == "low":
        return 85

    # For high detail, OpenAI resizes the image to fit within 2048x2048
    # while maintaining aspect ratio, then counts 512x512 tiles
    width, height = image.size

    # Resize logic to fit within 2048x2048
    if width > 2048 or height > 2048:
        if width > height:
            height = int(height * 2048 / width)
            width = 2048
        else:
            width = int(width * 2048 / height)
            height = 2048

    # Further resize so shortest side is 768px
    if width < height:
        if width > 768:
            height = int(height * 768 / width)
            width = 768
    else:
        if height > 768:
            width = int(width * 768 / height)
            height = 768

    # Count 512x512 tiles
    tiles_width = math.ceil(width / 512)
    tiles_height = math.ceil(height / 512)
    total_tiles = tiles_width * tiles_height

    # Each tile costs 170 tokens, plus 85 base tokens
    return total_tiles * 170 + 85


def estimate_anthropic_image_tokens(image: Image.Image) -> int:
    """
    Estimate tokens for an image using Anthropic's counting method.

    Args:
        image: PIL Image

    Returns:
        Estimated token count
    """
    width, height = image.size

    # Anthropic's token counting is based on image dimensions
    # They use approximately 1.2 tokens per "tile" where tiles are roughly 1024x1024
    # But they also have a base cost per image

    # Rough approximation based on Anthropic's documentation
    # They count tokens based on the image size after potential resizing
    total_pixels = width * height

    # Anthropic typically charges around 1.15 tokens per 1000 pixels
    # with a minimum base cost
    base_tokens = 100  # Base cost for any image
    pixel_tokens = math.ceil(total_pixels / 1000 * 1.15)

    return base_tokens + pixel_tokens


def estimate_image_tokens(image: Image.Image, model: str, detail: str = "high") -> int:
    """
    Estimate tokens for an image based on the model provider.

    Args:
        image: PIL Image
        model: Model string (e.g., "openai/gpt-4-vision-preview", "anthropic/claude-3-sonnet")
        detail: Detail level for OpenAI models ("high" or "low")

    Returns:
        Estimated token count
    """
    if model.startswith("anthropic"):
        return estimate_anthropic_image_tokens(image)
    else:
        return estimate_openai_image_tokens(image, detail)


def estimate_total_tokens(
    prompt: str, images: list[Image.Image], model: str, detail: str = "high"
) -> int:
    """
    Estimate total tokens for a prompt with images.

    Args:
        prompt: Text prompt
        images: List of PIL Images
        model: Model string
        detail: Detail level for OpenAI models

    Returns:
        Estimated total token count
    """
    # Estimate text tokens
    text_tokens = approx_token_count(prompt)

    # Estimate image tokens
    image_tokens = sum(estimate_image_tokens(img, model, detail) for img in images)

    return text_tokens + image_tokens
