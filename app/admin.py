"""Admin API. Only HR and the CEO get past `auth.admin_employee`; rules inside decide what each may change:
    - One account system: HR and CEO employees sign in with their work account; standalone admin accounts also work.
      Level "super" (the CEO, or a super admin) has CEO-level rights.
    - HR can edit and add employees, reset passwords, manage policy PDFs, read questions and activity.
    - Only the CEO can change access levels, edit the CEO's own record, or delete employees.
Every change is written to the audit log (salary values are never written there)."""
import logging
import re
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from . import auth, db, ingest, people, seed

log = logging.getLogger("hr-rag")
router = APIRouter(prefix="/api/admin")
admin = auth.current_admin

Status = Literal["Permanent", "Probation", "Intern", "Contract"]
Role = Literal["employee", "manager", "hr", "executive"]
WorkModel = Literal["Office", "Hybrid", "Remote"]
NOT_NULL = {"full_name", "title", "department", "grade", "employment_status", "access_role", "work_location",
            "work_model", "join_date", "grade_since", "gross_salary", "on_pip", "annual_used", "casual_used",
            "sick_used", "pf_enrolled"}
SECRET_FIELDS = {"gross_salary"}


class EmployeeUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=2, max_length=80)
    title: str | None = Field(None, min_length=2, max_length=80)
    department: str | None = Field(None, min_length=2, max_length=60)
    grade: int | None = Field(None, ge=1, le=7)
    employment_status: Status | None = None
    access_role: Role | None = None
    work_location: str | None = Field(None, min_length=2, max_length=40)
    work_model: WorkModel | None = None
    manager_id: int | None = None
    join_date: date | None = None
    confirmation_date: date | None = None
    end_date: date | None = None
    grade_since: date | None = None
    gross_salary: int | None = Field(None, ge=0, le=20_000_000)
    last_rating: int | None = Field(None, ge=1, le=5)
    on_pip: bool | None = None
    annual_used: float | None = Field(None, ge=0, le=365)
    casual_used: float | None = Field(None, ge=0, le=365)
    sick_used: float | None = Field(None, ge=0, le=365)
    covered_dependents: int | None = Field(None, ge=0, le=12)
    pf_enrolled: bool | None = None


class EmployeeCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=80)
    title: str = Field(min_length=2, max_length=80)
    department: str = Field(min_length=2, max_length=60)
    grade: int = Field(ge=1, le=7)
    employment_status: Status
    access_role: Role = "employee"
    work_location: str = Field(min_length=2, max_length=40)
    manager_id: int
    join_date: date
    end_date: date | None = None
    gross_salary: int = Field(ge=0, le=20_000_000)


class AdminLogin(BaseModel):
    username: str = Field(max_length=60)
    password: str = Field(max_length=200)


class PasswordReset(BaseModel):
    password: str | None = Field(None, min_length=8, max_length=64)


def _ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()


def _cookie(response: Response, request: Request, name: str, token: str, hours: int, samesite: str) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(name, token, max_age=hours * 3600, httponly=True, samesite=samesite, secure=secure)


@router.post("/login")
def admin_login(body: AdminLogin, request: Request, response: Response):
    """Sign in with a work email and password (HR and CEO employees) or with a standalone admin username.
    A work-account sign-in also signs you in to the chat: one account system, one session."""
    key = f"admin|{_ip(request)}|{body.username.strip().lower()}"
    if auth.throttled(key):
        raise HTTPException(429, "Too many failed attempts. Please wait 10 minutes.")
    admin_id = auth.admin_authenticate(body.username, body.password)
    if admin_id is not None:
        auth.clear_failures(key)
        _cookie(response, request, auth.ADMIN_COOKIE, auth.new_admin_session(admin_id), auth.ADMIN_HOURS, "strict")
        return {"ok": True}
    emp_id = auth.authenticate(body.username, body.password)
    if emp_id is None:
        auth.record_failure(key)
        raise HTTPException(401, "Incorrect email or password.")
    emp = db.get_employee(emp_id)
    if emp["access_role"] not in ("hr", "executive"):
        raise HTTPException(403, "This account does not have admin access. Only HR and the CEO can open the admin panel.")
    auth.clear_failures(key)
    _cookie(response, request, auth.COOKIE, auth.new_session(emp_id), auth.SESSION_HOURS, "lax")
    return {"ok": True}


@router.post("/logout")
def admin_logout(request: Request, response: Response):
    """Signs out of everything: admin session and work-account session."""
    auth.end_admin_session(request.cookies.get(auth.ADMIN_COOKIE))
    auth.end_session(request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.ADMIN_COOKIE)
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@router.get("/me")
def admin_me(actor: dict = Depends(admin)):
    return {"name": actor["full_name"], "username": actor["username"], "level": actor["level"], "employee": actor["employee"]}


def _all():
    emps = db.all_employees()
    return emps, {e["id"]: e for e in emps}


def _public(e: dict, by_id: dict) -> dict:
    mgr = by_id.get(e["manager_id"])
    return {**e, "manager_name": mgr["full_name"] if mgr else None}


def default_password(name: str, emps: list[dict], code: str) -> str:
    short = seed.short_name(name).lower()
    clash = any(seed.short_name(e["full_name"]).lower() == short for e in emps)
    return f"zakheera@{short}" + (code.split("-")[-1] if clash else "")


def _guard_target(actor: dict, target: dict) -> None:
    if target["access_role"] == "executive" and actor["access_role"] != "executive":
        raise HTTPException(403, "Only the CEO can change this record.")


def _check_manager(emp_id: int | None, manager_id: int, emps: list[dict], by_id: dict) -> None:
    if manager_id not in by_id:
        raise HTTPException(400, "That manager does not exist.")
    if emp_id is not None and (manager_id == emp_id or manager_id in people.subtree(emp_id, emps)):
        raise HTTPException(400, "An employee cannot report to themselves or to someone who reports to them.")


# ------------------------------------------------------------------ overview
@router.get("/overview")
def overview(actor: dict = Depends(admin)):
    emps, _ = _all()
    stats = db.qa_stats(14)
    days7 = stats["per_day"][-7:]
    ans = sum(d["answered"] for d in days7)
    un = sum(d["unanswered"] for d in days7)
    den = sum(d["denied"] for d in days7)
    by_status: dict[str, int] = {}
    for e in emps:
        by_status[e["employment_status"]] = by_status.get(e["employment_status"], 0) + 1
    pol = db.policies_list()
    return {"employees": len(emps), "by_status": by_status, "policies": len(pol), "chunks": sum(p["chunks"] for p in pol),
            "week": {"answered": ans, "unanswered": un, "denied": den,
                     "answer_rate": round(100 * ans / (ans + un), 1) if ans + un else None},
            "per_day": stats["per_day"], "top_unanswered": stats["top_unanswered"]}


# ------------------------------------------------------------------ employees
@router.get("/employees")
def list_employees(actor: dict = Depends(admin)):
    emps, by_id = _all()
    return {"employees": [_public(e, by_id) for e in emps]}


@router.put("/employees/{emp_id}")
def update_employee(emp_id: int, body: EmployeeUpdate, actor: dict = Depends(admin)):
    emps, by_id = _all()
    target = by_id.get(emp_id)
    if not target:
        raise HTTPException(404, "Employee not found.")
    _guard_target(actor, target)
    fields = body.model_dump(exclude_unset=True)
    for k in [k for k in fields if fields[k] is None and k in NOT_NULL]:
        raise HTTPException(422, f"{k} cannot be empty.")
    if "access_role" in fields and fields["access_role"] != target["access_role"]:
        if actor["access_role"] != "executive":
            raise HTTPException(403, "Only the CEO can change access levels.")
        if emp_id == actor["id"]:
            raise HTTPException(400, "You cannot change your own access level.")
    if "manager_id" in fields:
        if fields["manager_id"] is None:
            if target["manager_id"] is not None:
                raise HTTPException(400, "Everyone except the CEO needs a manager.")
        else:
            _check_manager(emp_id, fields["manager_id"], emps, by_id)
    merged = {**target, **fields}
    if merged["employment_status"] in ("Intern", "Contract") and not merged["end_date"]:
        raise HTTPException(422, "Interns and contract employees need an end date.")
    if merged["employment_status"] == "Permanent" and not merged["confirmation_date"]:
        fields["confirmation_date"] = people.add_months(merged["join_date"], 3)
    changed = {k: v for k, v in fields.items() if target.get(k) != v}
    if not changed:
        return {"employee": _public(target, by_id), "changed": []}
    db.update_employee(emp_id, changed)
    detail = "; ".join(f"{k} changed" if k in SECRET_FIELDS else f"{k}: {target.get(k)} -> {changed[k]}" for k in changed)
    db.audit(actor, "employee_updated", f"{target['emp_code']} {target['full_name']}", detail)
    emps, by_id = _all()
    return {"employee": _public(by_id[emp_id], by_id), "changed": list(changed)}


@router.post("/employees")
def create_employee(body: EmployeeCreate, actor: dict = Depends(admin)):
    emps, by_id = _all()
    if body.access_role in ("hr", "executive") and actor["access_role"] != "executive":
        raise HTTPException(403, "Only the CEO can grant HR or executive access.")
    if body.employment_status in ("Intern", "Contract") and not body.end_date:
        raise HTTPException(422, "Interns and contract employees need an end date.")
    _check_manager(None, body.manager_id, emps, by_id)
    code = f"ZAI-{db.next_emp_number():04d}"
    base = seed.email_for(body.full_name)
    email, n = base, 1
    taken = {e["email"].lower() for e in emps}
    while email.lower() in taken:
        n += 1
        email = base.replace("@", f"{n}@")
    password = default_password(body.full_name, emps, code)
    values = {
        "emp_code": code, "email": email, "password_hash": auth.hash_password(password), "full_name": body.full_name.strip(),
        "title": body.title, "department": body.department, "grade": body.grade, "employment_status": body.employment_status,
        "access_role": body.access_role, "work_location": body.work_location,
        "work_model": "Office" if body.employment_status in ("Intern", "Probation") else "Hybrid",
        "manager_id": body.manager_id, "join_date": body.join_date, "grade_since": body.join_date, "end_date": body.end_date,
        "confirmation_date": people.add_months(body.join_date, 3) if body.employment_status == "Permanent" else None,
        "gross_salary": body.gross_salary, "on_pip": False, "annual_used": 0, "casual_used": 0, "sick_used": 0,
        "covered_dependents": None if body.employment_status == "Intern" else 0,
        "pf_enrolled": body.employment_status == "Permanent",
    }
    new_id = db.insert_employee(values)
    db.audit(actor, "employee_created", f"{code} {values['full_name']}", f"{body.title}, {body.department}")
    return {"id": new_id, "emp_code": code, "email": email, "password": password}


@router.post("/employees/{emp_id}/reset-password")
def reset_password(emp_id: int, body: PasswordReset, actor: dict = Depends(admin)):
    emps, by_id = _all()
    target = by_id.get(emp_id)
    if not target:
        raise HTTPException(404, "Employee not found.")
    _guard_target(actor, target)
    others = [e for e in emps if e["id"] != emp_id]
    password = body.password or default_password(target["full_name"], others, target["emp_code"])
    db.set_password(emp_id, auth.hash_password(password))
    db.audit(actor, "password_reset", f"{target['emp_code']} {target['full_name']}", "custom password" if body.password else "default password")
    return {"password": password}


@router.delete("/employees/{emp_id}")
def delete_employee(emp_id: int, actor: dict = Depends(admin)):
    emps, by_id = _all()
    target = by_id.get(emp_id)
    if not target:
        raise HTTPException(404, "Employee not found.")
    if actor["access_role"] != "executive":
        raise HTTPException(403, "Only the CEO can delete employees.")
    if emp_id == actor["id"]:
        raise HTTPException(400, "You cannot delete your own account.")
    if any(e["manager_id"] == emp_id for e in emps):
        raise HTTPException(400, "This person still has people reporting to them. Move them to another manager first.")
    db.delete_employee(emp_id)
    db.audit(actor, "employee_deleted", f"{target['emp_code']} {target['full_name']}")
    return {"ok": True}


# ------------------------------------------------------------------ policies
NAME_RE = re.compile(r"^[A-Za-z0-9 _().-]{1,110}\.pdf$")


@router.get("/policies")
def list_policies(actor: dict = Depends(admin)):
    return {"policies": db.policies_list()}


@router.put("/policies/{filename}")
async def upload_policy(filename: str, request: Request, actor: dict = Depends(admin)):
    """Body is the raw PDF. Reads, chunks, embeds and replaces any earlier version with the same file name."""
    if not NAME_RE.match(filename):
        raise HTTPException(400, "Use a plain file name ending in .pdf (letters, numbers, spaces, - _ . ( ) only).")
    data = await request.body()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "File is larger than 10 MB.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "That file is not a PDF.")
    return await run_in_threadpool(_index, filename, data, actor)


def _index(filename: str, data: bytes, actor: dict) -> dict:
    try:
        result = ingest.ingest_pdf_bytes(filename, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception:
        log.exception("policy upload failed")
        raise HTTPException(502, "Could not read or index this PDF. Please try again.")
    db.audit(actor, "policy_uploaded", filename, f"{result['chunks']} chunks, version {result['version']}")
    return result


@router.delete("/policies/{filename}")
def delete_policy(filename: str, actor: dict = Depends(admin)):
    removed = db.delete_document(filename)
    if not removed:
        raise HTTPException(404, "Policy not found.")
    db.audit(actor, "policy_deleted", filename, f"{removed} chunks removed")
    return {"removed": removed}


# ------------------------------------------------------------------ questions and activity
@router.get("/questions")
def questions(kind: Literal["unanswered", "denied", "all"] = "unanswered", actor: dict = Depends(admin)):
    """Asker names are deliberately not shown (only department): questions can be sensitive."""
    return {"questions": db.qa_list(kind, 100)}


@router.get("/activity")
def activity(actor: dict = Depends(admin)):
    return {"activity": db.audit_list(100)}
