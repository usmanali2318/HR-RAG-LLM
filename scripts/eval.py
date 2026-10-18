"""Calibrate and test the live system (needs GEMINI_API_KEY and DATABASE_URL, documents already ingested).

    python -m scripts.eval            # retrieval only: shows score separation and suggests MIN_SCORE
    python -m scripts.eval --full     # full pipeline: checks answers contain expected text and unanswerable ones are refused
"""
import argparse
import json
import uuid
from pathlib import Path

from app import db, embeddings, rag
from app.config import settings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()
    qs = json.loads((Path(__file__).resolve().parent.parent / "tests" / "eval_questions.json").read_text())
    db.get_pool()
    viewer = next(e for e in db.all_employees() if e["emp_code"] == "ZAI-0012")   # Ahmed Raza, plain employee

    yes, no, doc_hits = [], [], 0
    for item in qs:
        hit = db.search(embeddings.embed_query(item["q"]), 1)[0]
        (yes if item["answerable"] else no).append(hit["score"])
        ok_doc = item["answerable"] and item.get("doc", "").lower() in hit["doc_name"].lower()
        doc_hits += ok_doc
        tag = "OK " if ok_doc else ("   " if not item["answerable"] else "BAD")
        print(f"{hit['score']:.3f} {tag} {'ans' if item['answerable'] else 'out'}  {item['q'][:60]:60s} -> {hit['doc_name']}")

    n_yes = len(yes)
    print(f"\nRetrieval: right document on top for {doc_hits}/{n_yes} answerable questions")
    print(f"Lowest answerable score : {min(yes):.3f}")
    print(f"Highest unanswerable    : {max(no):.3f}")
    if min(yes) > max(no):
        print(f"Clean separation. Suggested MIN_SCORE = {(min(yes) + max(no)) / 2:.2f}  (current: {settings.min_score})")
    else:
        print(f"Scores overlap. Pick MIN_SCORE near {min(yes) - 0.01:.2f} to avoid refusing valid questions;"
              " the LLM sufficiency check and citation check will catch the rest.")

    if args.full:
        passed = 0
        for item in qs:
            r = rag.answer(viewer["id"], uuid.uuid4().hex, item["q"])
            if item["answerable"]:
                ok = r.answered and item["expect"].lower() in r.answer.lower()
            else:
                ok = not r.answered
            passed += ok
            print(f"{'PASS' if ok else 'FAIL'}  {item['q'][:60]:60s} answered={r.answered} reason={r.reason}")
            if not ok:
                print(f"      answer: {r.answer[:200]}")
        print(f"\nFull pipeline: {passed}/{len(qs)} passed")


if __name__ == "__main__":
    main()
