"""Password hashing (PBKDF2, standard library only), cookie sessions and login throttling."""
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from . import db

COOKIE = "zk_session"
SESSION_HOURS = 12
ITERATIONS = 200_000
MAX_FAILS, WINDOW = 5, 600   # 5 wrong attempts per 10 minutes per (ip, email)

_fails: dict[str, deque] = defaultdict(deque)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), ITERATIONS).hex()
    return f"pbkdf2${ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, digest = stored.split("$")
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters)).hex()
        return hmac.compare_digest(check, digest)
    except Exception:
        return False


_DUMMY = hash_password("not-a-real-password")


def throttled(key: str) -> bool:
    q, now = _fails[key], time.time()
    while q and now - q[0] > WINDOW:
        q.popleft()
    return len(q) >= MAX_FAILS


def record_failure(key: str) -> None:
    _fails[key].append(time.time())


def clear_failures(key: str) -> None:
    _fails.pop(key, None)


def authenticate(email: str, password: str) -> int | None:
    row = db.get_login(email.strip())
    ok = verify_password(password, row[1] if row else _DUMMY)   # same work whether or not the user exists
    return row[0] if (row and ok) else None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(employee_id: int) -> str:
    token = secrets.token_urlsafe(32)
    db.create_session(_hash_token(token), employee_id, SESSION_HOURS)
    return token


def employee_for_token(token: str | None) -> dict | None:
    if not token:
        return None
    emp_id = db.session_employee_id(_hash_token(token))
    return db.get_employee(emp_id) if emp_id else None


def end_session(token: str | None) -> None:
    if token:
        db.delete_session(_hash_token(token))


def current_employee(request: Request) -> dict:
    emp = employee_for_token(request.cookies.get(COOKIE))
    if not emp:
        raise HTTPException(401, "Please log in.")
    return emp


ADMIN_COOKIE = "zk_admin"
ADMIN_HOURS = 4


def admin_authenticate(username: str, password: str) -> int | None:
    row = db.get_admin_login(username)
    ok = verify_password(password, row[1] if row else _DUMMY)
    return row[0] if (row and ok) else None


def new_admin_session(admin_id: int) -> str:
    token = secrets.token_urlsafe(32)
    db.create_admin_session(_hash_token(token), admin_id, ADMIN_HOURS)
    return token


def admin_for_token(token: str | None) -> dict | None:
    """An admin account is NOT an employee. Its id is negative so it can never equal an employee id."""
    if not token:
        return None
    admin_id = db.admin_session_id(_hash_token(token))
    a = db.get_admin(admin_id) if admin_id else None
    if not a:
        return None
    return {"id": -a["id"], "admin_id": a["id"], "username": a["username"], "full_name": a["full_name"], "level": a["level"],
            "access_role": "executive" if a["level"] == "super" else "hr", "employee": False}


def admin_actor(request: Request) -> dict | None:
    """One account system: a standalone admin account, OR an employee whose access level is HR or CEO."""
    a = admin_for_token(request.cookies.get(ADMIN_COOKIE))
    if a:
        return a
    e = employee_for_token(request.cookies.get(COOKIE))
    if e and e["access_role"] in ("hr", "executive"):
        return {"id": e["id"], "admin_id": None, "username": e["email"], "full_name": e["full_name"],
                "level": "super" if e["access_role"] == "executive" else "hr", "access_role": e["access_role"], "employee": True}
    return None


def current_admin(request: Request) -> dict:
    a = admin_actor(request)
    if a:
        return a
    if employee_for_token(request.cookies.get(COOKIE)):
        raise HTTPException(403, "The admin panel is only for HR and the CEO.")
    raise HTTPException(401, "Please sign in to the admin panel.")


def end_admin_session(token: str | None) -> None:
    if token:
        db.delete_admin_session(_hash_token(token))
