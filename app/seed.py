"""Create the employee tables and load the 50 dummy employees.

    python -m app.seed            # load if the table is empty
    python -m app.seed --reset    # wipe employees, sessions and chat history, then reload

Also writes data/credentials.csv (dummy plaintext passwords, for testing only). Only salted hashes go in the database.
"""
import argparse
import csv
import random
import time
from datetime import date, timedelta
from pathlib import Path

import psycopg

from . import auth, db, people
from .config import settings
from .seed_data import ACCESS_OVERRIDES, E, FIXED, HAJJ, WORK_MODEL_OVERRIDES

RATING_TO_INCREMENT = {5: 15, 4: 11, 3: 7, 2: 1}
HONORIFICS = {"syed", "dr", "mr", "ms", "mrs"}


def short_name(name: str) -> str:
    """The name people go by: the first word that is not an honorific ("Syed Usman Ali" -> "Usman")."""
    parts = name.replace(".", "").split()
    return next((p for p in parts if p.lower() not in HONORIFICS), parts[0])


def email_for(name: str) -> str:
    return f"{short_name(name).lower()}.{name.split()[-1].lower()}@zakheera.ai"


def make_passwords(people_list: list[tuple[str, str]]) -> dict[str, str]:
    """people_list = [(employee code, full name)].
    Password is zakheera@<short name>. If two employees share a short name, each gets their employee id
    added so passwords stay different, e.g. zakheera@ahmed0012 and zakheera@ahmed0031."""
    counts: dict[str, int] = {}
    for _, name in people_list:
        counts[short_name(name).lower()] = counts.get(short_name(name).lower(), 0) + 1
    out = {}
    for code, name in people_list:
        key = short_name(name).lower()
        out[code] = f"zakheera@{key}" + (code if counts[key] > 1 else "")
    return out


def credentials() -> dict[str, str]:
    return make_passwords([(row[0], row[1]) for row in E])


def demo_accounts() -> list[dict]:
    pw = credentials()
    picks = [("0001", "CEO"), ("0002", "Head of HR"), ("0010", "Engineering manager"),
             ("0012", "Senior engineer"), ("0015", "On probation"), ("0016", "Intern")]
    by = {r[0]: r for r in E}
    return [{"label": label, "name": by[c][1], "email": email_for(by[c][1]), "password": pw[c]} for c, label in picks]


def build_rows(today: date) -> list[dict]:
    rng = random.Random(20261017)
    has_reports = {r[7] for r in E if r[7]}
    rows = []
    for code, name, title, dept, grade, status, loc, mgr, join, gross, end in E:
        fx = FIXED.get(code, {})
        months_service = people.whole_months(join, today)
        confirmation = people.add_months(join, 3) if status == "Permanent" else None

        grade_since = join
        if status == "Permanent" and months_service > 20 and rng.random() < 0.5:
            lo, hi = people.add_months(join, 18), today - timedelta(days=90)
            if lo < hi:
                grade_since = lo + timedelta(days=rng.randint(0, (hi - lo).days))

        rating = None
        if status in ("Permanent", "Contract") and join <= date(2025, 12, 31):
            rating = rng.choice([3, 3, 3, 3, 4, 4, 5])
        rating = fx.get("rating", rating) if "rating" in fx else rating
        pip = fx.get("pip", False)

        if status == "Intern":
            accrued = people.credited_months(join, today, calendar_year_only=False)
            annual_used, casual_used, sick_used = float(rng.randint(0, int(accrued))), 0.0, 0.0
        else:
            m = people.credited_months(join, today)
            yrs = people.completed_years(join, today)
            a_full = people.LEAVE_DAYS["annual_5y"] if yrs >= 5 else people.LEAVE_DAYS["annual"]
            half = lambda x: round(x * 2) / 2
            annual_used = 0.0 if status == "Probation" else half(rng.uniform(0.15, 0.8) * a_full * m / 12)
            casual_used = half(rng.uniform(0.1, 0.9) * people.LEAVE_DAYS["casual"] * m / 12)
            sick_used = half(rng.uniform(0.0, 0.7) * people.LEAVE_DAYS["sick"] * m / 12)

        access = ACCESS_OVERRIDES.get(code) or ("manager" if code in has_reports else "employee")
        bond = fx.get("bond")
        rows.append(dict(
            emp_code=f"ZAI-{code}", full_name=name, email=email_for(name), title=title, department=dept, grade=grade,
            employment_status=status, access_role=access, work_location=loc,
            work_model=WORK_MODEL_OVERRIDES.get(code, "Office" if status in ("Intern", "Probation") else "Hybrid"),
            manager_code=mgr, join_date=join, confirmation_date=confirmation, end_date=end, grade_since=grade_since,
            gross_salary=gross, last_rating=rating, last_increment_pct=RATING_TO_INCREMENT.get(rating) if rating else None,
            on_pip=pip, annual_used=annual_used, casual_used=casual_used, sick_used=sick_used,
            covered_dependents=None if status == "Intern" else rng.randint(0, 4),
            pf_enrolled=status == "Permanent", hajj_taken=code in HAJJ,
            training_bond_amount=bond[0] if bond else None, training_bond_end=bond[1] if bond else None,
            _code=code,
        ))
    return rows


def load(reset: bool = False, today: date | None = None) -> int:
    """Hash all passwords first (slow, no database involved), then write everything in one short transaction."""
    today = today or date.today()
    db.init_schema()
    pw = credentials()
    rows = build_rows(today)
    for r in rows:
        r["password_hash"] = auth.hash_password(pw[r["_code"]])
        r["id"] = int(r["_code"])           # fixed ids 1..50, so managers can be linked in the same transaction
    cols = ["id", "emp_code", "full_name", "email", "password_hash", "title", "department", "grade", "employment_status",
            "access_role", "work_location", "work_model", "join_date", "confirmation_date", "end_date", "grade_since",
            "gross_salary", "last_rating", "last_increment_pct", "on_pip", "annual_used", "casual_used", "sick_used",
            "covered_dependents", "pf_enrolled", "hajj_taken", "training_bond_amount", "training_bond_end"]
    insert = f"INSERT INTO employees ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    managers = [(int(r["manager_code"]), r["id"]) for r in rows if r["manager_code"]]

    last = None
    for attempt in range(1, 4):
        try:
            with psycopg.connect(settings.database_url, prepare_threshold=None, connect_timeout=20,
                                 keepalives=1, keepalives_idle=15) as conn:
                if reset:
                    conn.execute("TRUNCATE employees, sessions RESTART IDENTITY CASCADE")
                    conn.execute("DELETE FROM chat_messages")
                    conn.execute("DELETE FROM qa_log")
                elif conn.execute("SELECT count(*) FROM employees").fetchone()[0] > 0:
                    return 0
                with conn.cursor() as cur:
                    cur.executemany(insert, [[r[c] for c in cols] for r in rows])
                    cur.executemany("UPDATE employees SET manager_id = %s WHERE id = %s", managers)
                conn.execute("SELECT setval(pg_get_serial_sequence('employees', 'id'), (SELECT max(id) FROM employees))")
            return len(rows)      # leaving the with-block commits
        except psycopg.OperationalError as exc:
            last = exc
            print(f"Connection problem (attempt {attempt} of 3): {str(exc).splitlines()[0]}")
            time.sleep(3 * attempt)
    raise last


def write_credentials_csv(path: Path) -> None:
    pw = credentials()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["emp_code", "name", "email", "password", "title", "department", "access_role"])
        rows = {f"ZAI-{r['_code']}": r for r in build_rows(date.today())}
        for code, name, title, dept, *_ in E:
            r = rows[f"ZAI-{code}"]
            w.writerow([f"ZAI-{code}", name, email_for(name), pw[code], title, dept, r["access_role"]])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    n = load(reset=args.reset)
    write_credentials_csv(Path(__file__).resolve().parent.parent / "data" / "credentials.csv")
    print(f"Loaded {n} employees." if n else "Employees already loaded (use --reset to reload).")
    print("Dummy logins written to data/credentials.csv")
    db.close_pool()


if __name__ == "__main__":
    main()
