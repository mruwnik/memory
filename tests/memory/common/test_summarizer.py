from memory.common import summarizer
import pytest


@pytest.mark.parametrize(
    "response, expected",
    (
        # Basic valid cases
        ("", {"summary": "", "tags": []}),
        (
            "<summary>test</summary><tags><tag>tag1</tag><tag>tag2</tag></tags>",
            {"summary": "test", "tags": ["tag1", "tag2"]},
        ),
        (
            "<summary>test</summary><tags><tag>tag1</tag><tag>tag2</tag></tags>",
            {"summary": "test", "tags": ["tag1", "tag2"]},
        ),
        # Missing summary tag
        (
            "<tags><tag>tag1</tag><tag>tag2</tag></tags>",
            {"summary": "", "tags": ["tag1", "tag2"]},
        ),
        # Missing tags section
        (
            "<summary>test summary</summary>",
            {"summary": "test summary", "tags": []},
        ),
        # Empty summary tag
        (
            "<summary></summary><tags><tag>tag1</tag></tags>",
            {"summary": "", "tags": ["tag1"]},
        ),
        # Empty tags section
        (
            "<summary>test</summary><tags></tags>",
            {"summary": "test", "tags": []},
        ),
        # Single tag
        (
            "<summary>test</summary><tags><tag>single-tag</tag></tags>",
            {"summary": "test", "tags": ["single-tag"]},
        ),
        # Multiple tags
        (
            "<summary>test</summary><tags><tag>tag1</tag><tag>tag2</tag><tag>tag3</tag><tag>tag4</tag><tag>tag5</tag></tags>",
            {"summary": "test", "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"]},
        ),
        # Tags with special characters and hyphens
        (
            "<summary>test</summary><tags><tag>machine-learning</tag><tag>ai/ml</tag><tag>data_science</tag></tags>",
            {"summary": "test", "tags": ["machine-learning", "ai/ml", "data_science"]},
        ),
        # Summary with special characters
        (
            "<summary>Test with &amp; special characters &lt;&gt;</summary><tags><tag>test</tag></tags>",
            {"summary": "Test with & special characters <>", "tags": ["test"]},
        ),
        # Whitespace handling
        (
            "<summary>  test with spaces  </summary><tags><tag>  tag1  </tag><tag>tag2</tag></tags>",
            {"summary": "  test with spaces  ", "tags": ["  tag1  ", "tag2"]},
        ),
        # Mixed case XML tags (should still work with BeautifulSoup)
        (
            "<Summary>test</Summary><Tags><Tag>tag1</Tag></Tags>",
            {"summary": "test", "tags": ["tag1"]},
        ),
        # Multiple summary tags (should take first one)
        (
            "<summary>first</summary><summary>second</summary><tags><tag>tag1</tag></tags>",
            {"summary": "first", "tags": ["tag1"]},
        ),
        # Multiple tags sections (should collect all tags)
        (
            "<summary>test</summary><tags><tag>tag1</tag></tags><tags><tag>tag2</tag></tags>",
            {"summary": "test", "tags": ["tag1", "tag2"]},
        ),
        # XML with extra elements (should ignore them)
        (
            "<root><summary>test</summary><other>ignored</other><tags><tag>tag1</tag></tags></root>",
            {"summary": "test", "tags": ["tag1"]},
        ),
        # XML with attributes (should still work)
        (
            '<summary id="1">test</summary><tags type="keywords"><tag>tag1</tag></tags>',
            {"summary": "test", "tags": ["tag1"]},
        ),
        # Empty tag elements
        (
            "<summary>test</summary><tags><tag></tag><tag>valid-tag</tag><tag></tag></tags>",
            {"summary": "test", "tags": ["", "valid-tag", ""]},
        ),
        # Self-closing tags
        (
            "<summary>test</summary><tags><tag/><tag>valid-tag</tag></tags>",
            {"summary": "test", "tags": ["", "valid-tag"]},
        ),
        # Long content
        (
            f"<summary>{'a' * 1000}</summary><tags><tag>long-content</tag></tags>",
            {"summary": "a" * 1000, "tags": ["long-content"]},
        ),
        # XML with newlines and formatting
        (
            """<summary>
            Multi-line
            summary content
            </summary>
            <tags>
                <tag>formatted</tag>
                <tag>xml</tag>
            </tags>""",
            {
                "summary": "\n            Multi-line\n            summary content\n            ",
                "tags": ["formatted", "xml"],
            },
        ),
        # Malformed XML (missing closing tags) - BeautifulSoup parses as best it can
        (
            "<summary>test<tags><tag>tag1</tag></tags>",
            {"summary": "testtag1", "tags": ["tag1"]},
        ),
        # Invalid XML characters should be handled by BeautifulSoup
        (
            "<summary>test & unescaped</summary><tags><tag>tag1</tag></tags>",
            {"summary": "test & unescaped", "tags": ["tag1"]},
        ),
        # Only whitespace
        (
            "   \n\t   ",
            {"summary": "", "tags": []},
        ),
        # Non-XML content
        (
            "This is just plain text without XML",
            {"summary": "", "tags": []},
        ),
        # XML comments (should be ignored)
        (
            "<!-- comment --><summary>test</summary><!-- another comment --><tags><tag>tag1</tag></tags>",
            {"summary": "test", "tags": ["tag1"]},
        ),
        # CDATA sections
        (
            "<summary><![CDATA[test with <special> characters]]></summary><tags><tag>cdata</tag></tags>",
            {"summary": "test with <special> characters", "tags": ["cdata"]},
        ),
    ),
)
def test_parse_response(response, expected):
    assert summarizer.parse_response(response) == expected
