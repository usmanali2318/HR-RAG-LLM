import hashlib
import math
import os
import re

import pytest

os.environ.setdefault("DATABASE_URL", os.environ.get("TEST_DATABASE_URL", ""))
os.environ.setdefault("MIN_SCORE", "0.2")   # the fake embedder has a different score scale than Gemini
os.environ.setdefault("RATE_LIMIT_PER_MIN", "1000")

STOP = set("the a an of to is are do i my me how what can in on for and or be it at by with this that you your we our".split())


def _stem(w: str) -> str:
    for suf in ("ing", "es", "s", "ed"):
        if w.endswith(suf) and len(w) > len(suf) + 3:
            return w[: -len(suf)]
    return w


def fake_embed(text: str, dim: int = 768) -> list[float]:
    """Deterministic hashed bag-of-words vector: similar wording gives a high cosine score."""
    v = [0.0] * dim
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in STOP:
            continue
        h = int(hashlib.md5(_stem(w).encode()).hexdigest(), 16)
        v[h % dim] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


@pytest.fixture(scope="session")
def ingested():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("Set TEST_DATABASE_URL to a Postgres with pgvector to run these tests")
    from pathlib import Path

    import numpy as np

    from app import db
    from app.ingest import chunk_document, file_hash, parse_pdf

    db.reset_chunks()
    from app import seed
    seed.load(reset=True)
    for p in sorted(Path("data/policies").glob("*.pdf")):
        chunks = chunk_document(parse_pdf(p), p.name)
        rows = [{"doc_name": c.doc_name, "policy_id": c.policy_id, "version": c.version, "section": c.section,
                 "chunk_index": c.chunk_index, "content": c.content,
                 "embedding": np.array(fake_embed(c.embed_text), dtype="float32")} for c in chunks]
        db.replace_document(p.name, file_hash(p), rows)
    yield db.count_chunks()
    db.close_pool()
