import logging
import re
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import admin, auth, db, rag, seed
from .config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("hr-rag")
STATIC = Path(__file__).resolve().parent.parent / "static"
CONV_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_schema()
    db.get_pool()
    if db.count_employees() == 0:
        log.warning("No employees found. Run: python -m app.seed")
    if db.count_admins() == 0:
        if settings.admin_username and settings.admin_password:
            db.create_admin(settings.admin_username, settings.admin_name, auth.hash_password(settings.admin_password), "super")
            log.info("Created super admin %r from ADMIN_USERNAME / ADMIN_PASSWORD", settings.admin_username)
        else:
            log.warning("No admin account. Set ADMIN_USERNAME and ADMIN_PASSWORD in .env, or run: python -m app.admin_users add <username> <password>")
    yield
    db.close_pool()


app = FastAPI(title="ZAKHEERA HR Assistant", lifespan=lifespan)
app.include_router(admin.router)
_hits: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()


def _rate_limit(key: str, limit: int) -> None:
    now, q = time.time(), _hits[key]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many messages. Please wait a minute and try again.")
    q.append(now)


current_employee = auth.current_employee


def _optional_employee(request: Request) -> dict | None:
    return auth.employee_for_token(request.cookies.get(auth.COOKIE))


# ------------------------------------------------------------------ auth
class LoginIn(BaseModel):
    email: str
    password: str


@app.post("/api/login")
def login(body: LoginIn, request: Request, response: Response):
    key = f"{_client_ip(request)}|{body.email.strip().lower()}"
    if auth.throttled(key):
        raise HTTPException(429, "Too many failed attempts. Please wait 10 minutes and try again.")
    emp_id = auth.authenticate(body.email, body.password)
    if emp_id is None:
        auth.record_failure(key)
        raise HTTPException(401, "Incorrect email or password.")
    auth.clear_failures(key)
    token = auth.new_session(emp_id)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_HOURS * 3600, httponly=True, samesite="lax", secure=secure)
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    auth.end_session(request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


STARTERS = {
    "employee": ["How many leaves do I have left?", "What is my notice period?", "How many days can I work from home?",
                 "Am I eligible for gratuity?"],
    "manager": ["How many people are in my team?", "Who in my team is on probation?", "What is my notice period?",
                "How many leaves do I have left?"],
    "hr": ["Who is currently on a PIP?", "Who is on probation?", "How many employees do we have in each department?",
           "What is the maternity leave policy?"],
    "executive": ["How many employees do we have in each department?", "Who is on a PIP?", "Who is on probation?",
                  "What is the Eid bonus policy?"],
}


@app.get("/api/me")
def me(emp: dict = Depends(current_employee)):
    return {"name": emp["full_name"], "title": emp["title"], "department": emp["department"], "code": emp["emp_code"],
            "role": emp["access_role"], "admin": emp["access_role"] in ("hr", "executive"), "starters": STARTERS.get(emp["access_role"], STARTERS["employee"])}


@app.get("/api/demo-logins")
def demo_logins():
    return {"accounts": seed.demo_accounts() if settings.show_demo_logins else []}


# ------------------------------------------------------------------ chat
class ChatIn(BaseModel):
    message: str
    conversation_id: str | None = None


@app.post("/api/chat")
def chat(body: ChatIn, request: Request, emp: dict = Depends(current_employee)):
    _rate_limit(f"emp{emp['id']}", settings.rate_limit_per_min)
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "Message is empty.")
    if len(message) > settings.max_message_chars:
        raise HTTPException(400, f"Message is too long (max {settings.max_message_chars} characters).")
    conv = body.conversation_id or uuid.uuid4().hex
    if not CONV_RE.match(conv):
        raise HTTPException(400, "Invalid conversation id.")
    try:
        r = rag.answer(emp["id"], conv, message)
    except Exception:
        log.exception("chat failed")
        return JSONResponse({"error": "The assistant is temporarily unavailable. Please try again shortly."}, status_code=503)
    return {
        "conversation_id": conv,
        "answer": r.answer,
        "answered": r.answered,
        "sources": [{"doc": s.doc, "policy_id": s.policy_id, "section": s.section} for s in r.sources],
        "used_record": r.used_record,
        "follow_ups": r.follow_ups,
    }


@app.get("/api/history/{conversation_id}")
def history(conversation_id: str, emp: dict = Depends(current_employee)):
    if not CONV_RE.match(conversation_id):
        raise HTTPException(400, "Invalid conversation id.")
    return {"messages": db.get_history(conversation_id, emp["id"], 50)}


@app.get("/api/health")
def health():
    return {"status": "ok" if db.ping() else "db_down", "chunks": db.count_chunks(), "employees": db.count_employees()}


# ------------------------------------------------------------------ pages
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "img" / "favicon.png", media_type="image/png")


@app.get("/")
def index(request: Request):
    if not _optional_employee(request):
        return RedirectResponse("/login")
    return FileResponse(STATIC / "index.html")


@app.get("/admin")
def admin_page(request: Request):
    if auth.admin_actor(request):
        return FileResponse(STATIC / "admin.html")
    return RedirectResponse("/" if _optional_employee(request) else "/admin/login")


@app.get("/admin/login")
def admin_login_page(request: Request):
    if auth.admin_actor(request):
        return RedirectResponse("/admin")
    return FileResponse(STATIC / "admin-login.html")


@app.get("/login")
def login_page(request: Request):
    if _optional_employee(request):
        return RedirectResponse("/")
    return FileResponse(STATIC / "login.html")
