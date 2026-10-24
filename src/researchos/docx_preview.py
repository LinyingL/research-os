"""Bounded, read-only Word body preview. No extraction or external programs.

Only document.xml is read. Images, relationships, macros and embedded objects
are never loaded. This is a text/table preview, not a Word layout renderer.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_XML_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 200 * 1024
MAX_BLOCKS = 3000
MAX_TABLE_CELLS = 10_000
WORD_NAMESPACES = {"http://schemas.openxmlformats.org/wordprocessingml/2006/main",
                   "http://purl.oclc.org/ooxml/wordprocessingml/main"}


class PreviewError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


class _NoDoctype(ET.TreeBuilder):
    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise PreviewError("invalid", "DOCTYPE is not permitted in a Word preview")


def read(path: Path, *, equation: str = "[Equation]", image: str = "[Image]") -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise PreviewError("too_large", "Word archive exceeds the 64 MiB preview limit")
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_XML_BYTES:
                raise PreviewError("too_large", "Word body exceeds the 8 MiB XML preview limit")
            with archive.open(info) as stream:
                body = stream.read(MAX_XML_BYTES + 1)
            if len(body) > MAX_XML_BYTES:
                raise PreviewError("too_large", "Word body exceeds the XML preview limit")
        root = ET.fromstring(body, parser=ET.XMLParser(target=_NoDoctype()))
    except PreviewError:
        raise
    except (zipfile.BadZipFile, KeyError, OSError, RuntimeError, NotImplementedError, ET.ParseError, ValueError) as exc:
        raise PreviewError("invalid", f"Cannot read Word document body: {exc}") from exc
    namespace = root.tag.partition("}")[0].lstrip("{")
    if namespace not in WORD_NAMESPACES or root.tag != f"{{{namespace}}}document":
        raise PreviewError("invalid", "Not a supported WordprocessingML document")
    w = lambda name: f"{{{namespace}}}{name}"
    main = root.find(w("body"))
    if main is None:
        raise PreviewError("invalid", "Word document has no body")
    stack = [(main, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if depth > 64 or nodes > 100_000:
            raise PreviewError("too_large", "Word body exceeds the preview complexity limit")
        stack.extend((child, depth + 1) for child in item)
    remaining = MAX_TEXT_CHARS
    table_cells = 0
    blocks: list[dict[str, Any]] = []
    truncated = False

    def text(node: ET.Element) -> str:
        nonlocal remaining, truncated
        if remaining <= 0:
            truncated = True
            return ""
        tag = node.tag.rsplit("}", 1)[-1]
        if node.tag in (w("del"), w("moveFrom")):
            return ""
        if tag in ("oMath", "oMathPara"):
            value = equation
        elif node.tag in (w("drawing"), w("pict"), w("object")):
            value = image
        elif node.tag == w("t"):
            value = node.text or ""
        elif node.tag == w("tab"):
            value = "\t"
        elif node.tag in (w("br"), w("cr")):
            value = "\n"
        else:
            return "".join(text(child) for child in node)
        if len(value) > remaining:
            truncated = True
        value = value[:remaining]
        remaining -= len(value)
        return value

    def visit(parent: ET.Element) -> None:
        nonlocal truncated, table_cells
        for child in parent:
            if len(blocks) >= MAX_BLOCKS or remaining <= 0:
                truncated = True
                return
            if child.tag in (w("del"), w("moveFrom")):
                continue
            if child.tag == w("p"):
                value = text(child)
                if not value.strip():
                    continue
                style = child.find(f"{w('pPr')}/{w('pStyle')}")
                name = style.get(w("val"), "") if style is not None else ""
                match = re.fullmatch(r"heading\s*([1-6])", name, re.IGNORECASE)
                level = int(match[1]) if match else 1 if name.lower() == "title" else None
                outline = child.find(f"{w('pPr')}/{w('outlineLvl')}")
                outline_level = outline.get(w("val")) if outline is not None else None
                if outline_level in ("0", "1", "2", "3", "4", "5"):
                    level = int(outline_level) + 1
                blocks.append({"kind": "heading" if level else "paragraph", "level": level, "text": value})
            elif child.tag == w("tbl"):
                rows = []
                for row in child.findall(w("tr")):
                    if remaining <= 0:
                        truncated = True
                        break
                    cells = []
                    for cell in row.findall(w("tc")):
                        if table_cells >= MAX_TABLE_CELLS:
                            truncated = True
                            break
                        table_cells += 1
                        # Keep paragraphs in a cell separate; nested tables stay text.
                        cells.append("\n".join(filter(None, (text(p) for p in cell))))
                    rows.append(cells)
                    if table_cells >= MAX_TABLE_CELLS:
                        truncated = True
                        break
                if rows:
                    blocks.append({"kind": "table", "rows": rows})
            else:
                # Content controls and tracked insertions may wrap paragraphs.
                visit(child)

    visit(main)
    return {"blocks": blocks, "truncated": truncated}
