"""PDF -> structured chunks -> embeddings -> Postgres.

Usage:
    python -m app.ingest data/policies            # ingest new or changed PDFs
    python -m app.ingest data/policies --force    # re-embed everything
    python -m app.ingest data/policies --reset    # drop the table first (needed if EMBED_DIM changed)
    python -m app.ingest data/policies --dry-run  # parse and print chunks only, no API calls
"""
import argparse
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pdfplumber

MAX_CHARS = 1800        # soft cap per chunk
HEADING_MIN, HEADING_MAX = 13.0, 20.0   # font sizes treated as section headings (title is larger, body smaller)
EDGE = 45               # points at top and bottom of a page treated as running header / footer
PARA_GAP = 6.5          # vertical gap (pt) that starts a new paragraph


@dataclass
class Chunk:
    doc_name: str
    policy_id: str | None
    version: str | None
    section: str
    chunk_index: int
    content: str
    source_file: str = ""

    @property
    def embed_text(self) -> str:
        pid = f" ({self.policy_id})" if self.policy_id else ""
        return f"{self.doc_name}{pid} | {self.section}\n{self.content}"


@dataclass
class Parsed:
    title: str
    policy_id: str | None
    version: str | None
    sections: list[tuple[str, list[str]]] = field(default_factory=list)  # (heading, units)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\n", " ")).strip()


def _table_units(page, table) -> list[str]:
    rows = [[_clean(c) for c in r] for r in table.extract()]
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    # A header row is rendered in a bold font; key/value tables (e.g. document control) are not.
    x0, top, x1, bottom = table.rows[0].bbox
    first_chars = [c for c in page.chars if c["top"] >= top - 1 and c["bottom"] <= bottom + 1 and x0 - 1 <= c["x0"] <= x1 + 1]
    has_header = bool(first_chars) and all("bold" in c["fontname"].lower() for c in first_chars if c["text"].strip())
    units = []
    if has_header and len(rows) > 1:
        header = rows[0]
        for r in rows[1:]:
            parts = [f"{h}: {v}" for h, v in zip(header, r) if v]
            units.append("- " + "; ".join(parts))
    else:
        for r in rows:
            parts = [c for c in r if c]
            if len(parts) == 2:
                units.append(f"{parts[0]}: {parts[1]}")
            else:
                units.append("- " + " | ".join(parts))
    return units


def parse_pdf(path, stem: str | None = None) -> Parsed:
    sections: list[tuple[str, list[str]]] = []
    heading, units = "Document information", []
    preface_text = []

    with pdfplumber.open(path) as pdf:
        meta = pdf.metadata or {}
        title = (meta.get("Title") or stem or Path(path).stem).strip()
        for page in pdf.pages:
            h = float(page.height)
            tables = page.find_tables()
            boxes = [t.bbox for t in tables]

            def outside(o, boxes=boxes):
                if o.get("object_type") != "char":
                    return True
                cx, cy = (o["x0"] + o["x1"]) / 2, (o["top"] + o["bottom"]) / 2
                return not any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)

            items = []  # (top, kind, payload)
            for ln in page.filter(outside).extract_text_lines():
                if ln["top"] < EDGE or ln["bottom"] > h - EDGE:
                    continue  # running header / footer
                size = max(c["size"] for c in ln["chars"])
                items.append((ln["top"], "line", (ln["text"].strip(), size, ln["top"], ln["bottom"])))
            for t in tables:
                items.append((t.bbox[1], "table", t))
            items.sort(key=lambda x: x[0])

            prev_bottom = None
            for _, kind, payload in items:
                if kind == "table":
                    units.extend(_table_units(page, payload))
                    prev_bottom = None
                    continue
                text, size, top, bottom = payload
                if not text:
                    continue
                if HEADING_MIN <= size <= HEADING_MAX:
                    if units:
                        sections.append((heading, units))
                    heading, units, prev_bottom = text, [], None
                    continue
                if size > HEADING_MAX:      # document title block: skip, we use PDF metadata
                    continue
                new_unit = (
                    not units or text.startswith("- ") or units[-1].startswith("- ") and text.startswith("- ")
                    or prev_bottom is None or top - prev_bottom > PARA_GAP
                )
                if new_unit:
                    units.append(text)
                else:
                    units[-1] += " " + text
                prev_bottom = bottom
        if units:
            sections.append((heading, units))

    preface = " ".join(u for hd, us in sections if hd == "Document information" for u in us)
    pid = re.search(r"Policy ID:?\s*([A-Z0-9][A-Z0-9-]+)", preface) or None
    ver = re.search(r"Version:?\s*([0-9][0-9.]*)", preface) or None
    return Parsed(
        title=title,
        policy_id=pid.group(1) if pid else (meta.get("Subject") or None),
        version=ver.group(1) if ver else None,
        sections=sections,
    )


def _pack(units: list[str], limit: int) -> list[str]:
    chunks, cur, size = [], [], 0
    for u in units:
        if cur and size + len(u) > limit:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(u)
        size += len(u) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def chunk_document(parsed: Parsed, source_file: str) -> list[Chunk]:
    out: list[Chunk] = []
    for heading, units in parsed.sections:
        n_q = sum(1 for u in units if u.startswith("Q:"))
        if n_q >= 2:  # FAQ section: one chunk per Q/A pair keeps answers intact
            pairs, cur = [], []
            for u in units:
                if u.startswith("Q:") and cur:
                    pairs.append("\n".join(cur))
                    cur = []
                cur.append(u)
            if cur:
                pairs.append("\n".join(cur))
            bodies = pairs
        else:
            bodies = _pack(units, MAX_CHARS)
        for i, body in enumerate(bodies):
            label = heading if len(bodies) == 1 or n_q >= 2 else f"{heading} (part {i + 1})"
            out.append(Chunk(parsed.title, parsed.policy_id, parsed.version, label, 0, body, source_file))
    for i, c in enumerate(out):
        c.chunk_index = i
    return out


def ingest_pdf_bytes(filename: str, data: bytes) -> dict:
    """Parse, embed and store one uploaded PDF, replacing any earlier version with the same file name."""
    import io

    from . import db, embeddings

    parsed = parse_pdf(io.BytesIO(data), stem=Path(filename).stem)
    chunks = chunk_document(parsed, filename)
    if not chunks:
        raise ValueError("No readable text found in this PDF (is it a scan?).")
    vectors = embeddings.embed_documents([c.embed_text for c in chunks])
    rows = [{"doc_name": c.doc_name, "policy_id": c.policy_id, "version": c.version, "section": c.section,
             "chunk_index": c.chunk_index, "content": c.content, "embedding": np.array(v, dtype="float32")}
            for c, v in zip(chunks, vectors)]
    db.replace_document(filename, hashlib.sha256(data).hexdigest(), rows)
    return {"title": parsed.title, "policy_id": parsed.policy_id, "version": parsed.version, "chunks": len(rows)}


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pdfs = sorted(args.folder.glob("*.pdf"))
    if not pdfs:
        sys.exit(f"No PDFs found in {args.folder}")

    if args.dry_run:
        for p in pdfs:
            doc = chunk_document(parse_pdf(p), p.name)
            print(f"{p.name}: {len(doc)} chunks, avg {sum(len(c.content) for c in doc) // max(1, len(doc))} chars")
        return

    from . import db, embeddings
    if args.reset:
        db.reset_chunks()
    else:
        db.init_schema()

    for p in pdfs:
        h = file_hash(p)
        if not args.force and db.stored_hash(p.name) == h:
            print(f"skip (unchanged): {p.name}")
            continue
        chunks = chunk_document(parse_pdf(p), p.name)
        vectors = embeddings.embed_documents([c.embed_text for c in chunks])
        rows = [
            {"doc_name": c.doc_name, "policy_id": c.policy_id, "version": c.version, "section": c.section,
             "chunk_index": c.chunk_index, "content": c.content, "embedding": np.array(v, dtype="float32")}
            for c, v in zip(chunks, vectors)
        ]
        db.replace_document(p.name, h, rows)
        print(f"ingested: {p.name} -> {len(rows)} chunks")
    print(f"total chunks in database: {db.count_chunks()}")


if __name__ == "__main__":
    main()
