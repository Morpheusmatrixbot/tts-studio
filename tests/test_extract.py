"""Document parsing for the three input formats."""

from __future__ import annotations

import io
import zipfile

from ttsstudio import extract


def test_normalize_flattens_typography():
    raw = "He said “hello” — then left…"
    out = extract.normalize(raw)
    assert '"hello"' in out
    assert "..." in out
    assert " " not in out
    assert "“" not in out


def test_split_paragraphs_drops_blanks():
    assert extract.split_paragraphs("one\n\n\n  \n\ntwo") == ["one", "two"]


def test_extract_txt_keeps_paragraphs():
    doc = extract.extract(b"First para.\n\nSecond para.", "book.txt")
    paras = doc["sections"][0]["paragraphs"]
    assert paras == ["First para.", "Second para."]
    assert doc["word_count"] == 4
    assert doc["title"] == "Book"


def test_extract_txt_handles_invalid_utf8():
    doc = extract.extract(b"caf\xff\xfe text", "x.txt")
    assert doc["sections"][0]["paragraphs"]


def test_title_from_filename():
    assert extract.title_from_filename("the_great-book.epub") == "The Great Book"


def _epub(chapters: list[tuple[str, str, str]], title="Test Book", author="A. Writer") -> bytes:
    """Build a minimal but structurally valid EPUB in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        manifest, spine = "", ""
        for idx, (name, _heading, _body) in enumerate(chapters):
            manifest += f'<item id="i{idx}" href="{name}" media-type="application/xhtml+xml"/>'
            spine += f'<itemref idref="i{idx}"/>'
        zf.writestr(
            "OEBPS/content.opf",
            f'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:title>{title}</dc:title><dc:creator>{author}</dc:creator></metadata>"
            f"<manifest>{manifest}</manifest><spine>{spine}</spine></package>",
        )
        for name, heading, body in chapters:
            zf.writestr(
                f"OEBPS/{name}",
                '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
                f"<h1>{heading}</h1><p>{body}</p></body></html>",
            )
    return buf.getvalue()


def test_extract_epub_builds_chapters_and_title_section():
    data = _epub(
        [
            ("chapter-1.xhtml", "Chapter One", "It was a dark night."),
            ("chapter-2.xhtml", "Chapter Two", "The morning came."),
        ]
    )
    doc = extract.extract(data, "book.epub")
    assert doc["title"] == "Test Book"
    # A spoken title section is prepended before the chapters.
    assert doc["sections"][0]["id"] == "00-title"
    assert "A. Writer" in doc["sections"][0]["paragraphs"][0]
    headings = [s["heading"] for s in doc["sections"][1:]]
    assert "Chapter One." in headings[0]
    assert doc["section_count"] == 3


def test_extract_epub_skips_boilerplate():
    data = _epub(
        [
            ("colophon.xhtml", "Colophon", "Set in Caslon."),
            ("chapter-1.xhtml", "Chapter One", "Real content here."),
        ]
    )
    doc = extract.extract(data, "book.epub")
    bodies = " ".join(p for s in doc["sections"] for p in s["paragraphs"])
    assert "Caslon" not in bodies
    assert "Real content here." in bodies


def test_extract_epub_numbers_roman_headings():
    # A chapter titled "IV" should be spoken as a chapter number, not the letters.
    data = _epub([("chapter-1.xhtml", "IV", "Something happened.")])
    doc = extract.extract(data, "book.epub")
    chapter = doc["sections"][1]
    assert chapter["heading"].startswith("Chapter")
