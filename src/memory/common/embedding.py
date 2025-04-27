def embed_text(text: str, model: str = "text-embedding-3-small", n_dimensions: int = 1536) -> list[float]:
    """
    Embed a text using OpenAI's API.
    """
    return [0.0] * n_dimensions  # Placeholder n_dimensions-dimensional vector


def embed_file(file_path: str, model: str = "text-embedding-3-small", n_dimensions: int = 1536) -> list[float]:
    """
    Embed a file using OpenAI's API.
    """
    return [0.0] * n_dimensions  # Placeholder n_dimensions-dimensional vector
