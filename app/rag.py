"""The query pipeline for a logged-in employee:
smalltalk -> scoped employee data -> rewrite -> embed -> retrieve -> gate -> grounded generation -> validate."""
import logging
import re
from dataclasses import dataclass, field
from datetime import date

from . import db, embeddings, llm, people
from .config import settings

log = logging.getLogger("hr-rag")

SMALLTALK = re.compile(r"^\s*(hi|hello|hey|salam|assalam[\w\s-]*|good (morning|afternoon|evening)|thanks?( you)?|thank u|shukriya|ok(ay)?|bye|goodbye)\W*$", re.I)


def refusal() -> str:
    return ("I couldn't find a reliable answer to that in ZAKHEERA's HR policy documents or your records, and I don't want to guess. "
            f"Please contact HR at {settings.hr_contact}.")


@dataclass
class Source:
    doc: str
    policy_id: str | None
    section: str
    score: float


@dataclass
class Result:
    answer: str
    answered: bool
    sources: list[Source] = field(default_factory=list)
    follow_ups: list[str] = field(default_factory=list)
    used_record: bool = False
    reason: str = "ok"


_LIST_START = re.compile(r":\s+-\s+(?=\*\*|[A-Z])")
_LIST_ITEM = re.compile(r"\s+-\s+(?=\*\*|[A-Z])")


def tidy(text: str) -> str:
    """If the model wrote a list inline ("...are: - A - B - C"), put each item on its own line."""
    text = text.strip()
    m = _LIST_START.search(text)
    if not m or "\n- " in text:
        return text
    head, rest = text[:m.start() + 1], text[m.end():]
    return head + "\n- " + _LIST_ITEM.sub("\n- ", rest)


def _smalltalk(message: str, viewer: dict) -> str | None:
    if not SMALLTALK.match(message):
        return None
    first = viewer["full_name"].split()[0]
    if re.search(r"thank|shukriya|bye|ok", message, re.I):
        return f"You're welcome, {first}! Ask me anything else about HR whenever you like."
    return (f"Hello {first}! I'm the ZAKHEERA HR Assistant. Ask me about policies, or about your own leave balance, "
            "service period, notice period and more.")


def answer(viewer_id: int, conv_id: str, message: str) -> Result:
    today = date.today()
    history = db.get_history(conv_id, viewer_id, settings.history_turns * 2)
    emps = db.all_employees()
    viewer = next(e for e in emps if e["id"] == viewer_id)

    quick = _smalltalk(message, viewer)
    if quick:
        result = Result(quick, True, reason="smalltalk")
        _persist(viewer_id, conv_id, message, result)
        return result

    ctx = people.build_context(viewer_id, message, emps, today)

    standalone = message
    if history:
        try:
            rewritten = llm.rewrite_question(history, message)
            if rewritten and len(rewritten) <= 500:
                standalone = rewritten
        except Exception:
            log.exception("rewrite failed, using original message")

    hits = db.search(embeddings.embed_query(standalone), settings.top_k)
    top = hits[0]["score"] if hits else 0.0
    log.info("emp=%s q=%r standalone=%r top_score=%.3f personal=%s", viewer["emp_code"], message, standalone, top, ctx.personal)

    if top < settings.min_score:
        if not ctx.personal:
            result = Result(refusal(), False, reason=f"low_similarity({top:.2f})")
            _persist(viewer_id, conv_id, message, result, top)
            return result
        context = []   # personal question with no matching policy: the model may still use the employee's own data
    else:
        context = [h for h in hits if h["score"] >= settings.min_score * 0.85]

    out = llm.generate_grounded(history, message, standalone, context, ctx, today)

    valid_ids = [i for i in dict.fromkeys(out.source_ids) if 1 <= i <= len(context)]
    if out.access_denied and out.answer.strip():
        result = Result(tidy(out.answer), False, reason="access_denied")
    elif not out.sufficient_context or not out.answer.strip():
        result = Result(refusal(), False, reason="model_insufficient")
    elif not valid_ids and not out.used_employee_data:
        result = Result(refusal(), False, reason="no_valid_citation")
    else:
        sources = [Source(context[i - 1]["doc_name"], context[i - 1]["policy_id"], context[i - 1]["section"],
                          context[i - 1]["score"]) for i in valid_ids]
        result = Result(tidy(out.answer), True, sources, [f for f in out.follow_ups if f.strip()][:3],
                        used_record=out.used_employee_data)
    _persist(viewer_id, conv_id, message, result, top)
    return result


def _persist(viewer_id: int, conv_id: str, message: str, result: Result, top: float | None = None) -> None:
    db.save_message(conv_id, viewer_id, "user", message)
    db.save_message(conv_id, viewer_id, "assistant", result.answer)
    try:
        db.log_qa(viewer_id, conv_id, message, result.answered, result.reason, top)
    except Exception:
        log.exception("could not write qa_log")
