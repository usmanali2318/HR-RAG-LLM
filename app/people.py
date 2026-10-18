"""Employee data access rules and calculations.

SECURITY MODEL
The language model only ever sees data the logged-in employee is allowed to see. Filtering happens here,
in plain code, BEFORE the prompt is built, so no prompt trick can make the model reveal anything more.

    employee   : own full record + directory entries (name, title, department, location, email, manager)
    manager    : the above + everyone in their reporting line (all levels below), without pay or benefits
    hr         : everyone, including pay and benefits
    executive  : everyone, including pay and benefits

POLICY CONSTANTS below mirror the policy PDFs (Leave Policy, Compensation and Payroll Guide). If a policy
changes, update the constant here as well, because balances are computed in code, not by the model.
"""
import re
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta

BASIC_SHARE = 0.60            # basic salary as a share of gross (Compensation Guide, section 2)
PF_RATE = 0.10                # employee and employer provident fund rate on basic
EID_BONUS_SHARE = 0.50        # per Eid, share of basic salary
GRATUITY_MIN_YEARS = 3
LEAVE_DAYS = {"annual": 20, "annual_5y": 22, "casual": 8, "sick": 10}
RATING_LABELS = {5: "Outstanding", 4: "Exceeds expectations", 3: "Meets expectations",
                 2: "Needs improvement", 1: "Unsatisfactory"}
CONFIDENTIAL_ROLES = ("hr", "executive")


# ---------------------------------------------------------------- date helpers
def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def completed_years(start: date, today: date) -> int:
    return today.year - start.year - ((today.month, today.day) < (start.month, start.day))


def whole_months(start: date, today: date) -> int:
    return (today.year - start.year) * 12 + today.month - start.month - (today.day < start.day)


def tenure_text(start: date, today: date) -> str:
    m = max(0, whole_months(start, today))
    y, mo = divmod(m, 12)
    parts = ([f"{y} year{'s' if y != 1 else ''}"] if y else []) + ([f"{mo} month{'s' if mo != 1 else ''}"] if mo or not y else [])
    return " ".join(parts)


def credited_months(start: date, today: date, calendar_year_only: bool = True) -> int:
    """Months of leave credited. Leave is credited on the first working day of each month; a part month at
    joining counts as a full month if 15 or more days remain in it (Leave Policy, sections 1 and 13)."""
    remaining = monthrange(start.year, start.month)[1] - start.day + 1
    fy, fm = (start.year, start.month) if remaining >= 15 else (start.year + (start.month == 12), start.month % 12 + 1)
    if calendar_year_only and (fy, fm) < (today.year, 1):
        fy, fm = today.year, 1
    return max(0, (today.year - fy) * 12 + today.month - fm + 1)


def half_day(x: float) -> float:
    """Leave is shown in whole and half days: round to the nearest 0.5, quarter days round up."""
    return int(x * 2 + 0.5) / 2


def fmt(n: float) -> str:
    return f"{round(n, 1):g}"


def pkr(n: float) -> str:
    return f"PKR {n:,.0f}"


# ---------------------------------------------------------------- per-employee calculations
def leave_lines(e: dict, today: date) -> list[str]:
    if e["employment_status"] == "Intern":
        accrued = credited_months(e["join_date"], today, calendar_year_only=False)
        used = e["annual_used"]
        return [f"Leave (interns get 1 paid day per month of internship): accrued so far {fmt(accrued)}, "
                f"used {fmt(used)}, available now {fmt(max(0, accrued - used))}. No separate casual or sick leave."]
    months = credited_months(e["join_date"], today)
    years = completed_years(e["join_date"], today)
    annual_full = LEAVE_DAYS["annual_5y"] if years >= 5 else LEAVE_DAYS["annual"]
    out = [f"Leave balances for calendar year {today.year}. Accrued so far = days per year x months credited / 12, "
           f"rounded to the nearest half day (half-day leave is allowed). {months} of 12 months are credited so far:"]
    for label, key, full, used in (("Annual", "annual", annual_full, e["annual_used"]),
                                   ("Casual", "casual", LEAVE_DAYS["casual"], e["casual_used"]),
                                   ("Sick", "sick", LEAVE_DAYS["sick"], e["sick_used"])):
        raw = full * months / 12
        accrued = half_day(raw)
        line = (f"  - {label} leave: {full} days a year x {months}/12 = {raw:.2f}, rounded to {fmt(accrued)} accrued; "
                f"used {fmt(used)}; available now {fmt(max(0, accrued - used))}")
        if key == "annual" and e["employment_status"] == "Probation":
            line += " (accrues, but cannot be taken until probation is confirmed, except approved emergencies)"
        out.append(line)
    return out


def contract_lines(e: dict, today: date) -> list[str]:
    st = e["employment_status"]
    if st == "Probation":
        return [f"Status: on probation since joining; 3 month probation ends on {add_months(e['join_date'], 3).isoformat()}"]
    end = e["end_date"].isoformat() if e.get("end_date") else "not recorded"
    if st == "Intern":
        return [f"Status: intern, internship ends on {end}"]
    if st == "Contract":
        return [f"Status: contract employee, contract ends on {end}"]
    conf = e["confirmation_date"].isoformat() if e.get("confirmation_date") else "date not recorded"
    return [f"Status: permanent, confirmed on {conf}"]


def confidential_lines(e: dict, today: date) -> list[str]:
    """Pay and benefits. Shown only to the employee themself, HR and the CEO."""
    gross = e["gross_salary"]
    basic = gross * BASIC_SHARE
    out = []
    if e["employment_status"] == "Intern":
        return [f"Monthly stipend: {pkr(gross)}"]
    out.append(f"Monthly gross salary: {pkr(gross)} (basic {pkr(basic)}, house rent {pkr(gross * 0.30)}, utilities {pkr(gross * 0.10)})")
    years = completed_years(e["join_date"], today)
    if e["pf_enrolled"]:
        vest = 100 if years >= 3 else 50 if years >= 2 else 0
        out.append(f"Provident fund: enrolled; employee contributes {pkr(basic * PF_RATE)} per month and the company matches it; "
                   f"employer share vested: {vest}%")
    else:
        out.append("Provident fund: not enrolled yet (starts after confirmation)")
    if e["employment_status"] == "Permanent":
        if years >= GRATUITY_MIN_YEARS:
            out.append(f"Gratuity: eligible ({years} completed years); estimated {pkr(basic * years)} if leaving today "
                       f"(one month's basic per completed year)")
        else:
            out.append(f"Gratuity: not yet eligible; becomes eligible on {add_months(e['join_date'], 36).isoformat()} (3 completed years)")
        out.append(f"Eid bonus: {pkr(basic * EID_BONUS_SHARE)} per Eid (50% of basic), pro-rata if under 12 months of service")
    else:
        out.append("Gratuity and Eid bonus: not applicable for this employment status until confirmed as permanent")
    if e["covered_dependents"] is not None:
        out.append(f"Health insurance: employee plus {e['covered_dependents']} covered dependent(s)")
    else:
        out.append("Health insurance: not enrolled")
    if e["training_bond_amount"]:
        out.append(f"Training bond: {pkr(e['training_bond_amount'])}, applies until {e['training_bond_end'].isoformat()}")
    out.append(f"Hajj leave already taken: {'yes' if e['hajj_taken'] else 'no'}")
    return out


def directory_line(e: dict, by_id: dict) -> str:
    mgr = by_id.get(e["manager_id"])
    return (f"{e['full_name']} | {e['title']} | {e['department']} | {e['work_location']} | {e['email']} | "
            f"reports to {mgr['full_name'] if mgr else 'nobody (top of the company)'}")


def record(e: dict, today: date, by_id: dict, confidential: bool) -> str:
    mgr = by_id.get(e["manager_id"])
    time_in_grade = whole_months(e["grade_since"], today)
    lines = [
        f"Name: {e['full_name']} ({e['emp_code']})",
        f"Email: {e['email']}",
        f"Job title: {e['title']}; department: {e['department']}; grade: G{e['grade']}",
        f"Work location: {e['work_location']}; current work model: {e['work_model']}",
        f"Reports to: {mgr['full_name'] + ' (' + mgr['title'] + ', ' + mgr['email'] + ')' if mgr else 'nobody (top of the company)'}",
        f"Joined: {e['join_date'].isoformat()} (service so far: {tenure_text(e['join_date'], today)}; completed years: {completed_years(e['join_date'], today)})",
        *contract_lines(e, today),
        f"In current grade since {e['grade_since'].isoformat()} ({time_in_grade} months)",
    ]
    if e["last_rating"]:
        inc = f"; last increment {fmt(e['last_increment_pct'])}% effective 1 July {today.year if today.month >= 7 else today.year - 1}" if e["last_increment_pct"] else ""
        lines.append(f"Last performance rating: {e['last_rating']} ({RATING_LABELS[e['last_rating']]}){inc}")
    else:
        lines.append("Last performance rating: none yet (not through a review cycle)")
    lines.append(f"On a Performance Improvement Plan: {'yes' if e['on_pip'] else 'no'}")
    lines += leave_lines(e, today)
    if confidential:
        lines += confidential_lines(e, today)
    return "\n".join(lines)


def roster_line(e: dict, today: date, by_id: dict, show_pay: bool) -> str:
    mgr = by_id.get(e["manager_id"])
    if e["employment_status"] == "Intern":
        leave = f"leave available {fmt(max(0, credited_months(e['join_date'], today, False) - e['annual_used']))}"
    else:
        m = credited_months(e["join_date"], today)
        yrs = completed_years(e["join_date"], today)
        a = half_day((LEAVE_DAYS['annual_5y'] if yrs >= 5 else LEAVE_DAYS['annual']) * m / 12) - e["annual_used"]
        c = half_day(LEAVE_DAYS["casual"] * m / 12) - e["casual_used"]
        s = half_day(LEAVE_DAYS["sick"] * m / 12) - e["sick_used"]
        leave = f"leave available annual {fmt(max(0, a))}, casual {fmt(max(0, c))}, sick {fmt(max(0, s))}"
    parts = [e["emp_code"], e["full_name"], e["title"], e["department"], f"G{e['grade']}", e["employment_status"],
             e["work_location"], e["work_model"], f"reports to {mgr['full_name'] if mgr else '-'}",
             f"joined {e['join_date'].isoformat()}", leave,
             f"rating {e['last_rating'] or '-'}", f"PIP {'yes' if e['on_pip'] else 'no'}"]
    if show_pay:
        parts.append(f"gross {pkr(e['gross_salary'])}")
    return " | ".join(parts)


def headcount_summary(emps: list[dict]) -> str:
    def count(key):
        c = {}
        for e in emps:
            c[e[key]] = c.get(e[key], 0) + 1
        return ", ".join(f"{k}: {v}" for k, v in sorted(c.items()))
    return (f"Headcount in scope: {len(emps)}. By department: {count('department')}. "
            f"By employment status: {count('employment_status')}. By location: {count('work_location')}.")


# ---------------------------------------------------------------- access rules
def subtree(root_id: int, emps: list[dict]) -> set[int]:
    children: dict[int, list[int]] = {}
    for e in emps:
        if e["manager_id"]:
            children.setdefault(e["manager_id"], []).append(e["id"])
    seen, stack = set(), [root_id]
    while stack:
        for c in children.get(stack.pop(), []):
            if c not in seen:
                seen.add(c)
                stack.append(c)
    return seen


def visible_ids(viewer: dict, emps: list[dict]) -> set[int]:
    if viewer["access_role"] in CONFIDENTIAL_ROLES:
        return {e["id"] for e in emps}
    if viewer["access_role"] == "manager":
        return subtree(viewer["id"], emps) | {viewer["id"]}
    return {viewer["id"]}


TITLE_KEYWORDS = [
    (re.compile(r"\bceo\b|chief executive"), "Chief Executive Officer"),
    (re.compile(r"\bcto\b|chief technology"), "Chief Technology Officer"),
    (re.compile(r"\bcfo\b|chief financial"), "Chief Financial Officer"),
    (re.compile(r"\bcoo\b|chief operating"), "Chief Operating Officer"),
    (re.compile(r"\bhead of (hr|human resources)\b|\bhr head\b"), "Head of Human Resources"),
]
ROSTER_RE = re.compile(
    r"\b(team|teams|reports|reportees?|subordinates?|employees|staff|everyone|everybody|headcount|colleagues?|"
    r"members|department|who is|who are|who has|who works|who all|how many people|list|anyone|anybody|people)\b", re.I)
PERSONAL_RE = re.compile(r"\b(my|me|mine|myself|i|i'm|im|am i|our)\b", re.I)


def find_mentions(message: str, emps: list[dict], exclude_id: int) -> list[dict]:
    low = message.lower()
    found: dict[int, dict] = {}
    for rx, title in TITLE_KEYWORDS:
        if rx.search(low):
            for e in emps:
                if e["title"] == title and e["id"] != exclude_id:
                    found[e["id"]] = e
    words = set(re.findall(r"[a-z]+", low))
    for e in emps:
        if e["id"] == exclude_id:
            continue
        name = e["full_name"].lower()
        if name in low:
            found[e["id"]] = e
            continue
        tokens = [t for t in re.findall(r"[a-z]+", name) if len(t) >= 3 and t != "dr"]
        if any(t in words for t in tokens):
            found[e["id"]] = e
    return list(found.values())[:6]


@dataclass
class Context:
    self_text: str
    people_text: str = ""
    notices: list[str] = field(default_factory=list)
    personal: bool = False
    used_people: list[str] = field(default_factory=list)


def build_context(viewer_id: int, message: str, emps: list[dict], today: date) -> Context:
    by_id = {e["id"]: e for e in emps}
    viewer = by_id[viewer_id]
    role = viewer["access_role"]
    allowed = visible_ids(viewer, emps)
    can_see_pay = role in CONFIDENTIAL_ROLES

    ctx = Context(self_text=record(viewer, today, by_id, confidential=True))
    ctx.personal = bool(PERSONAL_RE.search(message))
    blocks: list[str] = []

    mentions = find_mentions(message, emps, viewer_id)
    for m in mentions:
        ctx.personal = True
        if m["id"] in allowed:
            blocks.append(f"RECORD OF {m['full_name'].upper()}:\n" + record(m, today, by_id, confidential=can_see_pay))
            ctx.used_people.append(m["full_name"])
        else:
            blocks.append(f"DIRECTORY ENTRY: {directory_line(m, by_id)}")
            ctx.notices.append(
                f"The employee asked about {m['full_name']}. They are NOT authorised to see anything about "
                f"{m['full_name']} beyond the directory entry (name, title, department, location, email, manager). "
                f"Do not reveal or estimate any other detail, and say you cannot share it.")
        if m["id"] in allowed and not can_see_pay and m["id"] != viewer_id:
            ctx.notices.append(f"Pay, benefits and insurance details of {m['full_name']} are not visible to this employee (managers cannot see team pay).")

    if ROSTER_RE.search(message):
        ctx.personal = True
        if role == "employee":
            ctx.notices.append("This employee has no direct reports and no access to other employees' data or to team or company statistics. "
                               "They may only see directory entries of people they name.")
        else:
            scope = [e for e in emps if e["id"] in allowed and e["id"] != viewer_id] if role == "manager" else emps
            label = "everyone in the company" if role in CONFIDENTIAL_ROLES else f"people in this manager's reporting line ({len(scope)} people)"
            blocks.append(f"ROSTER ({label}):\n{headcount_summary(scope)}\n" +
                          "\n".join(roster_line(e, today, by_id, can_see_pay) for e in scope))
            if role == "manager":
                ctx.notices.append("This manager can only see their own reporting line, not company-wide data, and cannot see anyone's pay.")

    ctx.people_text = "\n\n".join(blocks)
    return ctx
