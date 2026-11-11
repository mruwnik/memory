import ast
import io
import pathlib

import pytest
from PIL import EpsImagePlugin, Image, UnidentifiedImageError

from memory.common import extract
from memory.common.images import RASTER_FORMATS, open_image
from memory.common.ingest_routing import is_image_bytes

SRC = pathlib.Path(__file__).parents[3] / "src" / "memory"

# Decoding this runs Ghostscript on `{ } loop`, which never returns.
EPS = (
    b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 3000 3000\n"
    b"%%EndComments\n{ } loop\n%%EOF\n"
)


@pytest.fixture
def eps_opens(monkeypatch) -> list[bytes]:
    """Records each time PIL's EPS plugin starts reading a file."""
    calls: list[bytes] = []
    original = EpsImagePlugin.EpsImageFile._open

    def spy(self):
        calls.append(b"eps")
        return original(self)

    monkeypatch.setattr(EpsImagePlugin.EpsImageFile, "_open", spy)
    return calls


def test_eps_labelled_png_is_refused_before_the_eps_plugin(eps_opens):
    with pytest.raises(UnidentifiedImageError):
        extract.extract_data_chunks("image/png", EPS)
    assert eps_opens == []


def test_eps_is_not_routed_as_an_image(eps_opens):
    assert is_image_bytes(EPS) is False
    assert eps_opens == []


def test_eps_file_with_png_name_is_refused(eps_opens, tmp_path):
    path = tmp_path / "photo.png"
    path.write_bytes(EPS)
    with pytest.raises(UnidentifiedImageError):
        open_image(path)
    assert eps_opens == []


def saved(image_format: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format=image_format)
    return buffer.getvalue()


@pytest.mark.parametrize("image_format", RASTER_FORMATS)
def test_allowed_formats_open(image_format):
    with open_image(io.BytesIO(saved(image_format))) as image:
        assert image.format == image_format
        assert image.size == (8, 8)


def pil_open_calls(path: pathlib.Path) -> list[str]:
    """`Image.open(...)` / `PILImage.open(...)` calls in a source file."""
    tree = ast.parse(path.read_text())
    return [
        f"{path.relative_to(SRC)}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "open"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in {"Image", "PILImage"}
    ]


def test_every_pil_open_goes_through_open_image():
    calls = [
        call
        for path in SRC.rglob("*.py")
        if path != SRC / "common" / "images.py"
        for call in pil_open_calls(path)
    ]
    assert calls == []
