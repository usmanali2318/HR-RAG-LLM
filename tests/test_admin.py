import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, embeddings
from tests.conftest import fake_embed
from tests.test_pipeline import FakeLLM, PW, ask, client, email, emps, login  # noqa: F401  (fixtures)

PDF = Path("data/policies/Leave Policy.pdf").read_bytes()


ADMIN_PW = "Test-Admin-Pass-1"


@pytest.fixture(autouse=True)
def _admin_accounts(ingested):
    from app import auth
    for name, level in (("t_super", "super"), ("t_hr", "hr")):
        if not db.get_admin_login(name):
            db.create_admin(name, f"Test {level}", auth.hash_password(ADMIN_PW), level)


def alogin(client, who):
    r = client.post("/api/admin/login", json={"username": {"super": "t_super", "hr": "t_hr"}[who], "password": ADMIN_PW})
    assert r.status_code == 200, r.text
    return client


def make_employee(c, name="Test Person", **kw):
    body = {"full_name": name, "title": "QA Analyst", "department": "Engineering", "grade": 3,
            "employment_status": "Permanent", "work_location": "Karachi", "manager_id": 10,
            "join_date": "2024-01-15", "gross_salary": 200000, **kw}
    r = c.post("/api/admin/employees", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def emp_id(c, code):
    return next(e["id"] for e in c.get("/api/admin/employees").json()["employees"] if e["emp_code"] == code)


# ------------------------------------------------------------ who gets in
@pytest.mark.parametrize("who", ["hr", "super"])
def test_admin_accounts_get_in(client, who):
    assert client.get("/api/admin/overview").status_code == 401
    alogin(client, who)
    assert client.get("/api/admin/overview").status_code == 200 and client.get("/api/admin/employees").status_code == 200
    assert client.get("/api/admin/me").json()["level"] == who


@pytest.mark.parametrize("code,level", [("0001", "super"), ("0007", "hr"), ("0002", "hr")])
def test_ceo_and_hr_work_logins_open_the_admin_panel(client, code, level):
    login(client, code)                                   # the normal chat login
    assert client.get("/api/me").json()["admin"] is True
    assert client.get("/api/admin/overview").status_code == 200       # no second login needed
    assert client.get("/api/admin/me").json()["level"] == level
    assert client.get("/admin").status_code == 200


@pytest.mark.parametrize("code", ["0012", "0009", "0010", "0013"])      # employee, HR intern, manager, employee
def test_other_work_logins_cannot_open_the_admin_panel(client, code):
    login(client, code)
    assert client.get("/api/me").json()["admin"] is False
    assert client.get("/api/admin/overview").status_code == 403
    assert client.get("/admin", follow_redirects=False).headers["location"] == "/"


def test_admin_login_page_accepts_work_credentials_and_signs_into_chat_too(client):
    assert client.post("/api/admin/login", json={"username": email("0001"), "password": PW["0001"]}).status_code == 200
    assert client.get("/api/admin/me").json() == {"name": "Syed Usman Ali", "username": email("0001"), "level": "super", "employee": True}
    assert client.get("/api/me").status_code == 200                    # one account system, one session
    assert client.post("/api/admin/logout").status_code == 200         # signs out of everything
    assert client.get("/api/me").status_code == 401 and client.get("/api/admin/me").status_code == 401


def test_admin_login_with_work_credentials_rules(client):
    assert client.post("/api/admin/login", json={"username": email("0012"), "password": PW["0012"]}).status_code == 403   # valid, not HR/CEO
    assert client.get("/api/admin/overview").status_code in (401,)                                                          # no session was created
    assert client.post("/api/admin/login", json={"username": email("0007"), "password": "wrong"}).status_code == 401
    assert client.post("/api/admin/login", json={"username": email("0007"), "password": PW["0007"]}).status_code == 200


def test_work_account_ceo_cannot_change_or_delete_themselves(client):
    login(client, "0001")
    me = emp_id(client, "ZAI-0001")
    assert client.put(f"/api/admin/employees/{me}", json={"access_role": "employee"}).status_code == 400
    assert client.delete(f"/api/admin/employees/{me}").status_code == 400


def test_admin_cookie_is_not_an_employee_login(client):
    alogin(client, "super")
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401
    assert client.get("/api/me").status_code == 401


def test_admin_login_logout_and_lockout(client):
    assert client.post("/api/admin/login", json={"username": "t_hr", "password": "nope"}).status_code == 401
    assert client.get("/admin", follow_redirects=False).headers["location"] == "/admin/login"
    alogin(client, "hr")
    assert client.get("/admin").status_code == 200
    assert client.get("/admin/login", follow_redirects=False).headers["location"] == "/admin"
    client.post("/api/admin/logout")
    assert client.get("/api/admin/me").status_code == 401
    for _ in range(5):
        client.post("/api/admin/login", json={"username": "t_lock", "password": "bad"})
    assert client.post("/api/admin/login", json={"username": "t_lock", "password": "bad"}).status_code == 429


def test_admin_passwords_are_hashed(ingested):
    with db.get_pool().connection() as conn:
        h = conn.execute("SELECT password_hash FROM admins WHERE username = 't_hr'").fetchone()[0]
    assert h.startswith("pbkdf2$") and ADMIN_PW not in h


# ------------------------------------------------------------ employees
def test_hr_edits_employee_and_audit_hides_salary(client):
    alogin(client, "hr")
    new = make_employee(client, "Edit Me")
    r = client.put(f"/api/admin/employees/{new['id']}", json={"title": "Senior QA Analyst", "gross_salary": 250000, "grade": 4})
    assert r.status_code == 200 and set(r.json()["changed"]) == {"title", "gross_salary", "grade"}
    log_text = " ".join(a["detail"] or "" for a in client.get("/api/admin/activity").json()["activity"])
    assert "title: QA Analyst -> Senior QA Analyst" in log_text and "gross_salary changed" in log_text
    assert "250000" not in log_text and "200000" not in log_text


def test_access_level_rules(client):
    alogin(client, "hr")
    new = make_employee(client, "Role Test")
    assert client.put(f"/api/admin/employees/{new['id']}", json={"access_role": "hr"}).status_code == 403     # HR cannot
    assert client.put(f"/api/admin/employees/{emp_id(client, 'ZAI-0001')}", json={"title": "X Officer"}).status_code == 403   # nor edit the CEO
    assert client.post("/api/admin/employees", json={"full_name": "Sneaky Hr", "title": "Officer", "department": "HR", "grade": 3,
                       "employment_status": "Permanent", "work_location": "Karachi", "manager_id": 2, "join_date": "2025-01-01",
                       "gross_salary": 1, "access_role": "hr"}).status_code == 403
    client.post("/api/admin/logout"); alogin(client, "super")
    assert client.put(f"/api/admin/employees/{new['id']}", json={"access_role": "hr"}).status_code == 200


def test_validation_rules(client):
    alogin(client, "hr")
    new = make_employee(client, "Valid Test")
    pid = new["id"]
    assert client.put(f"/api/admin/employees/{pid}", json={"grade": 9}).status_code == 422
    assert client.put(f"/api/admin/employees/{pid}", json={"title": None}).status_code == 422
    assert client.put(f"/api/admin/employees/{pid}", json={"employment_status": "Intern"}).status_code == 422   # needs end date
    ceo_team_lead = emp_id(client, "ZAI-0010")
    assert client.put(f"/api/admin/employees/{ceo_team_lead}", json={"manager_id": emp_id(client, "ZAI-0012")}).status_code == 400  # cycle
    assert client.put(f"/api/admin/employees/{pid}", json={"manager_id": pid}).status_code == 400
    assert client.put(f"/api/admin/employees/{pid}", json={"manager_id": None}).status_code == 400
    assert client.put("/api/admin/employees/99999", json={"title": "Nope Nope"}).status_code == 404


def test_create_employee_gets_default_password_and_can_log_in(client):
    alogin(client, "hr")
    first = make_employee(client, "Zarnab Tester")
    assert first["password"] == "zakheera@zarnab" and first["email"] == "zarnab.tester@zakheera.ai"
    clash = make_employee(client, "Ahmed Khan")                    # "Ahmed" already exists (Ahmed Raza)
    assert clash["password"] == "zakheera@ahmed" + clash["emp_code"].split("-")[1]
    other = TestClient(client.app)
    assert other.post("/api/login", json={"email": first["email"], "password": first["password"]}).status_code == 200
    assert other.get("/api/me").json()["role"] == "employee"


def test_reset_password_ends_old_sessions(client):
    alogin(client, "hr")
    new = make_employee(client, "Reset Person")
    victim = TestClient(client.app)
    assert victim.post("/api/login", json={"email": new["email"], "password": new["password"]}).status_code == 200
    assert client.post(f"/api/admin/employees/{new['id']}/reset-password", json={"password": "short"}).status_code == 422
    r = client.post(f"/api/admin/employees/{new['id']}/reset-password", json={"password": "Brand-New-Pass-9"})
    assert r.json()["password"] == "Brand-New-Pass-9"
    assert victim.get("/api/me").status_code == 401                                   # old session killed
    assert victim.post("/api/login", json={"email": new["email"], "password": new["password"]}).status_code == 401
    assert victim.post("/api/login", json={"email": new["email"], "password": "Brand-New-Pass-9"}).status_code == 200
    assert client.post(f"/api/admin/employees/{emp_id(client, 'ZAI-0001')}/reset-password", json={}).status_code == 403   # HR vs CEO


def test_delete_rules(client):
    alogin(client, "hr")
    new = make_employee(client, "Delete Person")
    assert client.delete(f"/api/admin/employees/{new['id']}").status_code == 403       # HR cannot delete
    client.post("/api/admin/logout"); alogin(client, "super")
    assert client.delete(f"/api/admin/employees/{emp_id(client, 'ZAI-0010')}").status_code == 400   # has reports
    assert client.delete(f"/api/admin/employees/{emp_id(client, 'ZAI-0001')}").status_code == 400   # self
    assert client.delete(f"/api/admin/employees/{new['id']}").status_code == 200
    assert all(e["id"] != new["id"] for e in client.get("/api/admin/employees").json()["employees"])


# ------------------------------------------------------------ policies
def test_policy_upload_list_delete(client, monkeypatch):
    monkeypatch.setattr(embeddings, "embed_documents", lambda texts, batch_size=32: [fake_embed(t) for t in texts])
    alogin(client, "hr")
    r = client.put("/api/admin/policies/Test Policy.pdf", content=PDF)
    assert r.status_code == 200 and r.json()["chunks"] > 5 and r.json()["policy_id"] == "ZAI-HR-LV-002"
    listed = {p["source_file"]: p for p in client.get("/api/admin/policies").json()["policies"]}
    assert listed["Test Policy.pdf"]["chunks"] == r.json()["chunks"]
    assert client.put("/api/admin/policies/Test Policy.pdf", content=PDF).status_code == 200      # replace, not duplicate
    assert {p["source_file"]: p for p in client.get("/api/admin/policies").json()["policies"]}["Test Policy.pdf"]["chunks"] == r.json()["chunks"]
    assert client.put("/api/admin/policies/notes.txt", content=PDF).status_code == 400
    assert client.put("/api/admin/policies/Fake.pdf", content=b"not a pdf").status_code == 400
    assert client.put("/api/admin/policies/..%2Fevil.pdf", content=PDF).status_code in (400, 404)
    assert client.delete("/api/admin/policies/Test Policy.pdf").json()["removed"] == r.json()["chunks"]
    assert client.delete("/api/admin/policies/Test Policy.pdf").status_code == 404


def test_employee_cannot_upload_policies(client):
    login(client, "0012")
    assert client.put("/api/admin/policies/X.pdf", content=PDF).status_code == 403


# ------------------------------------------------------------ questions log
def test_questions_log_shows_gaps_without_names(client, monkeypatch):
    FakeLLM(monkeypatch)
    login(client, "0012")
    q = f"What is the capital of France? {uuid.uuid4().hex[:6]}"
    ask(client, q)
    ok_q = f"How many annual leave days do I get per year? {uuid.uuid4().hex[:6]}"
    ask(client, ok_q)
    client.post("/api/admin/logout"); alogin(client, "hr")
    gaps = client.get("/api/admin/questions?kind=unanswered").json()["questions"]
    row = next(g for g in gaps if g["question"] == q)
    assert row["department"] == "Engineering" and "Ahmed" not in str(row) and "employee_id" not in row
    assert all(g["question"] != ok_q for g in gaps)
    assert any(g["question"] == ok_q for g in client.get("/api/admin/questions?kind=all").json()["questions"])
    ov = client.get("/api/admin/overview").json()
    assert ov["employees"] >= 50 and ov["week"]["unanswered"] >= 1 and ov["policies"] >= 10
    assert client.get("/api/admin/questions?kind=bogus").status_code == 422
