"""Turn an uploaded document into spoken-ready sections.

Supports .txt/.md, .epub (chapter by chapter, via the spine) and .pdf. The
output shape is always the same — a list of sections, each with a heading and
paragraphs — so the rest of the pipeline never has to care where text came from.
"""

from __future__ import annotations

import html
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

XHTML_NS = "{http://www.w3.org/1999/xhtml}"
# Front/back matter that is legal boilerplate rather than part of the book.
SKIP_STEMS = {
    "colophon", "imprint", "uncopyright", "loi", "titlepage",
    "halftitlepage", "toc", "cover", "copyright",
}
_ROMAN_RE = re.compile(r"^[IVXLCDM]+$")
SUPPORTED_SUFFIXES = {".txt", ".md", ".text", ".epub", ".pdf"}


def normalize(text: str) -> str:
    """Collapse a raw text run into one clean spoken paragraph."""
    t = html.unescape(text)
    for junk in ("﻿", "⁠", "​"):
        t = t.replace(junk, "")
    t = t.replace(" ", " ").replace(" ", " ")
    t = t.replace("—", " — ").replace("–", " — ").replace("−", " — ")
    t = t.replace("…", "...")
    t = t.replace("“", '"').replace("”", '"')
    t = t.replace("‘", "'").replace("’", "'")
    return re.sub(r"\s+", " ", t).strip()


def split_paragraphs(raw: str) -> list[str]:
    parts = [normalize(p) for p in re.split(r"\n\s*\n+", raw)]
    return [p for p in parts if p]


def title_from_filename(filename: str) -> str:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    return stem.title() if stem else "Untitled"


# --------------------------------------------------------------------------
# Plain text
# --------------------------------------------------------------------------


def extract_txt(raw: bytes, filename: str) -> dict:
    text = raw.decode("utf-8", errors="replace")
    paras = split_paragraphs(text) or [normalize(text)]
    paras = [p for p in paras if p]
    return {
        "title": title_from_filename(filename),
        "sections": [{"id": "section-01", "heading": "", "paragraphs": paras}],
    }


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------


def extract_pdf(raw: bytes, filename: str) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 — a damaged page should not lose the book
            pages.append("")
    full = "\n\n".join(pages)
    # PDFs hard-wrap lines mid-sentence; rejoin so chunking sees real sentences.
    full = re.sub(r"(\w)-\n(\w)", r"\1\2", full)
    full = re.sub(r"(?<![.!?:;])\n(?!\n)", " ", full)
    paras = [p for p in split_paragraphs(full) if len(p.split()) > 1]

    title = title_from_filename(filename)
    meta = getattr(reader, "metadata", None)
    if meta and getattr(meta, "title", None):
        cleaned = normalize(str(meta.title))
        if cleaned:
            title = cleaned
    return {"title": title, "sections": [{"id": "section-01", "heading": "", "paragraphs": paras}]}


# --------------------------------------------------------------------------
# EPUB
# --------------------------------------------------------------------------


def _local_name(tag: str) -> str:
    return tag.split("}")[-1] if tag else ""


def _paragraphs_from_xhtml(raw: bytes) -> tuple[str | None, list[str]]:
    root = ET.fromstring(raw)
    body = root.find(f".//{XHTML_NS}body")
    if body is None:
        body = root
    heading: str | None = None
    paras: list[str] = []

    def walk(el, buf: list[str]) -> None:
        if _local_name(el.tag) in {"script", "style", "svg", "img"}:
            return
        if el.text:
            buf.append(el.text)
        for child in list(el):
            walk(child, buf)
            if child.tail:
                buf.append(child.tail)

    for el in body.iter():
        name = _local_name(el.tag)
        if name in {"h1", "h2", "h3"}:
            bits: list[str] = []
            walk(el, bits)
            heading = normalize(" ".join(bits)) or heading
        elif name == "p":
            bits = []
            walk(el, bits)
            text = normalize(" ".join(bits))
            if text:
                paras.append(text)
    return heading, paras


def _spine_documents(zf: zipfile.ZipFile) -> list[str]:
    container = ET.fromstring(zf.read("META-INF/container.xml"))
    rootfile = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
    if rootfile is None:
        raise ValueError("EPUB is missing its rootfile")
    opf_path = rootfile.attrib["full-path"]
    opf_dir = str(Path(opf_path).parent).replace("\\", "/")
    opf_dir = "" if opf_dir == "." else opf_dir
    opf = ET.fromstring(zf.read(opf_path))
    ns = {"o": "http://www.idpf.org/2007/opf"}
    manifest = {i.attrib["id"]: i.attrib["href"] for i in opf.findall(".//o:manifest/o:item", ns)}
    names = []
    for itemref in opf.findall(".//o:spine/o:itemref", ns):
        href = manifest.get(itemref.attrib["idref"], "")
        if href.lower().endswith((".xhtml", ".html", ".htm")):
            names.append(f"{opf_dir}/{href}" if opf_dir else href)
    return names


def _epub_metadata(zf: zipfile.ZipFile) -> tuple[str, str]:
    container = ET.fromstring(zf.read("META-INF/container.xml"))
    rootfile = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
    opf = ET.fromstring(zf.read(rootfile.attrib["full-path"]))
    ns = {"dc": "http://purl.org/dc/elements/1.1/"}
    return (
        normalize(opf.findtext(".//dc:title", default="", namespaces=ns) or ""),
        normalize(opf.findtext(".//dc:creator", default="", namespaces=ns) or ""),
    )


def extract_epub(raw: bytes, filename: str) -> dict:
    sections: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        meta_title, meta_author = _epub_metadata(zf)
        chapter_i = 0
        dedication: str | None = None
        for name in _spine_documents(zf):
            stem = Path(name).stem.lower()
            try:
                heading, paras = _paragraphs_from_xhtml(zf.read(name))
            except (ET.ParseError, KeyError):
                continue
            if stem == "dedication":
                body = " ".join(paras).strip()
                if body:
                    dedication = body if body.lower().startswith("to ") else f"To {body}"
                continue
            if stem in SKIP_STEMS or not paras:
                continue
            if stem.startswith("chapter") or heading:
                chapter_i += 1
                if heading and not _ROMAN_RE.match(heading.strip()):
                    spoken = heading if heading.endswith((".", "!", "?")) else f"{heading}."
                else:
                    spoken = f"Chapter {chapter_i}."
                sections.append(
                    {"id": f"chapter-{chapter_i:02d}", "heading": spoken, "paragraphs": paras}
                )
            else:
                sections.append(
                    {
                        "id": stem or f"section-{len(sections) + 1:02d}",
                        "heading": heading or stem.replace("-", " ").title(),
                        "paragraphs": paras,
                    }
                )

    title = meta_title or title_from_filename(filename)
    opener = [f"{title}. By {meta_author}." if meta_author else f"{title}."]
    if dedication:
        opener.append(dedication if dedication.endswith(".") else f"{dedication}.")
    sections.insert(0, {"id": "00-title", "heading": "", "paragraphs": opener})
    return {"title": title, "author": meta_author, "sections": sections}


def extract(raw: bytes, filename: str) -> dict:
    """Dispatch on file extension. Unknown types are treated as plain text."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".epub":
        doc = extract_epub(raw, filename)
    elif suffix == ".pdf":
        doc = extract_pdf(raw, filename)
    else:
        doc = extract_txt(raw, filename)
    doc["sections"] = [s for s in doc["sections"] if s.get("paragraphs")]
    doc["word_count"] = sum(len(p.split()) for s in doc["sections"] for p in s["paragraphs"])
    doc["section_count"] = len(doc["sections"])
    return doc
