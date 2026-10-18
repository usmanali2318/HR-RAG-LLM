"""Postgres + pgvector access. Works with Supabase, Neon or any Postgres that has the vector extension."""
import psycopg
from psycopg_pool import ConnectionPool
from pgvector.psycopg import register_vector

from .config import settings

_pool: ConnectionPool | None = None

# prepare_threshold=None disables server-side prepared statements, which break behind
# transaction-mode poolers (Supabase pooler on port 6543, PgBouncer).
_CONN_KWARGS = {"autocommit": True, "prepare_threshold": None}


def init_schema() -> None:
    dim = settings.embed_dim
    with psycopg.connect(settings.database_url, **_CONN_KWARGS) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS policy_chunks (
                id          BIGSERIAL PRIMARY KEY,
                source_file TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                doc_name    TEXT NOT NULL,
                policy_id   TEXT,
                version     TEXT,
                section     TEXT NOT NULL,
                chunk_index INT  NOT NULL,
                content     TEXT NOT NULL,
                embedding   vector({dim}) NOT NULL
            )"""
        )
        conn.execute("ALTER TABLE policy_chunks ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()")
        conn.execute("CREATE INDEX IF NOT EXISTS policy_chunks_source_idx ON policy_chunks (source_file)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS policy_chunks_embedding_idx "
            "ON policy_chunks USING hnsw (embedding vector_cosine_ops)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id         BIGSERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )"""
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS qa_log (
                id BIGSERIAL PRIMARY KEY, employee_id BIGINT, conversation_id TEXT, question TEXT NOT NULL,
                answered BOOLEAN NOT NULL, reason TEXT NOT NULL, top_score REAL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS qa_log_created_idx ON qa_log (created_at DESC)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id BIGSERIAL PRIMARY KEY, actor_id BIGINT, actor_name TEXT NOT NULL, action TEXT NOT NULL,
                target TEXT, detail TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )"""
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                id BIGSERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, full_name TEXT NOT NULL,
                password_hash TEXT NOT NULL, level TEXT NOT NULL DEFAULT 'hr'
            )"""
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_sessions (
                token_hash TEXT PRIMARY KEY, admin_id BIGINT NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
                expires_at TIMESTAMPTZ NOT NULL
            )"""
        )
        conn.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS employee_id BIGINT")
        conn.execute("CREATE INDEX IF NOT EXISTS chat_messages_session_idx ON chat_messages (session_id, id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS employees (
                id                  BIGSERIAL PRIMARY KEY,
                emp_code            TEXT UNIQUE NOT NULL,
                full_name           TEXT NOT NULL,
                email               TEXT UNIQUE NOT NULL,
                password_hash       TEXT NOT NULL,
                title               TEXT NOT NULL,
                department          TEXT NOT NULL,
                grade               INT  NOT NULL,
                employment_status   TEXT NOT NULL,
                access_role         TEXT NOT NULL DEFAULT 'employee',
                work_location       TEXT NOT NULL,
                work_model          TEXT NOT NULL,
                manager_id          BIGINT REFERENCES employees(id),
                join_date           DATE NOT NULL,
                confirmation_date   DATE,
                end_date            DATE,
                grade_since         DATE NOT NULL,
                gross_salary        INT  NOT NULL,
                last_rating         INT,
                last_increment_pct  NUMERIC,
                on_pip              BOOLEAN NOT NULL DEFAULT FALSE,
                annual_used         NUMERIC NOT NULL DEFAULT 0,
                casual_used         NUMERIC NOT NULL DEFAULT 0,
                sick_used           NUMERIC NOT NULL DEFAULT 0,
                covered_dependents  INT,
                pf_enrolled         BOOLEAN NOT NULL DEFAULT FALSE,
                hajj_taken          BOOLEAN NOT NULL DEFAULT FALSE,
                training_bond_amount INT,
                training_bond_end   DATE
            )"""
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash  TEXT PRIMARY KEY,
                employee_id BIGINT NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                expires_at  TIMESTAMPTZ NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )"""
        )


def reset_chunks() -> None:
    with psycopg.connect(settings.database_url, **_CONN_KWARGS) as conn:
        conn.execute("DROP TABLE IF EXISTS policy_chunks")
    init_schema()


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=5,
            kwargs=_CONN_KWARGS,
            configure=register_vector,
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def ping() -> bool:
    with get_pool().connection() as conn:
        return conn.execute("SELECT 1").fetchone()[0] == 1


# ---- ingestion helpers -------------------------------------------------------
def stored_hash(source_file: str) -> str | None:
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT source_hash FROM policy_chunks WHERE source_file = %s LIMIT 1", (source_file,)
        ).fetchone()
    return row[0] if row else None


def replace_document(source_file: str, source_hash: str, rows: list[dict]) -> None:
    """Atomically swap all chunks of one document for a new set."""
    with get_pool().connection() as conn:
        with conn.transaction():
            conn.execute("DELETE FROM policy_chunks WHERE source_file = %s", (source_file,))
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO policy_chunks
                       (source_file, source_hash, doc_name, policy_id, version, section, chunk_index, content, embedding)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [
                        (source_file, source_hash, r["doc_name"], r["policy_id"], r["version"],
                         r["section"], r["chunk_index"], r["content"], r["embedding"])
                        for r in rows
                    ],
                )


def count_chunks() -> int:
    with get_pool().connection() as conn:
        return conn.execute("SELECT count(*) FROM policy_chunks").fetchone()[0]


# ---- retrieval ---------------------------------------------------------------
def search(query_vec, k: int) -> list[dict]:
    import numpy as np

    q = np.array(query_vec, dtype=np.float32)
    with get_pool().connection() as conn:
        rows = conn.execute(
            """SELECT id, doc_name, policy_id, section, content, 1 - (embedding <=> %s) AS score
               FROM policy_chunks ORDER BY embedding <=> %s LIMIT %s""",
            (q, q, k),
        ).fetchall()
    return [
        {"id": r[0], "doc_name": r[1], "policy_id": r[2], "section": r[3], "content": r[4], "score": float(r[5])}
        for r in rows
    ]


# ---- chat history (always scoped to one employee) ----------------------------
def save_message(session_id: str, employee_id: int, role: str, content: str) -> None:
    with get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO chat_messages (session_id, employee_id, role, content) VALUES (%s,%s,%s,%s)",
            (session_id, employee_id, role, content),
        )


def get_history(session_id: str, employee_id: int, limit: int) -> list[dict]:
    with get_pool().connection() as conn:
        rows = conn.execute(
            """SELECT role, content FROM (
                   SELECT id, role, content FROM chat_messages
                   WHERE session_id = %s AND employee_id = %s ORDER BY id DESC LIMIT %s
               ) t ORDER BY id ASC""",
            (session_id, employee_id, limit),
        ).fetchall()
    return [{"role": r[0], "content": r[1]} for r in rows]


# ---- employees and sessions ---------------------------------------------------
EMP_COLS = """id, emp_code, full_name, email, title, department, grade, employment_status, access_role,
              work_location, work_model, manager_id, join_date, confirmation_date, end_date, grade_since,
              gross_salary, last_rating, last_increment_pct, on_pip, annual_used, casual_used, sick_used,
              covered_dependents, pf_enrolled, hajj_taken, training_bond_amount, training_bond_end"""


def _rows(cur) -> list[dict]:
    cols = [d.name for d in cur.description]
    out = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        for k in ("last_increment_pct", "annual_used", "casual_used", "sick_used"):
            if d.get(k) is not None:
                d[k] = float(d[k])
        out.append(d)
    return out


def all_employees() -> list[dict]:
    with get_pool().connection() as conn:
        return _rows(conn.execute(f"SELECT {EMP_COLS} FROM employees ORDER BY emp_code"))


def get_employee(employee_id: int) -> dict | None:
    with get_pool().connection() as conn:
        rows = _rows(conn.execute(f"SELECT {EMP_COLS} FROM employees WHERE id = %s", (employee_id,)))
    return rows[0] if rows else None


def get_login(email: str) -> tuple[int, str] | None:
    with get_pool().connection() as conn:
        row = conn.execute("SELECT id, password_hash FROM employees WHERE lower(email) = lower(%s)", (email,)).fetchone()
    return (row[0], row[1]) if row else None


def count_employees() -> int:
    with get_pool().connection() as conn:
        return conn.execute("SELECT count(*) FROM employees").fetchone()[0]


def create_session(token_hash: str, employee_id: int, hours: int) -> None:
    with get_pool().connection() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < now()")
        conn.execute(
            "INSERT INTO sessions (token_hash, employee_id, expires_at) VALUES (%s,%s, now() + make_interval(hours => %s))",
            (token_hash, employee_id, hours),
        )


def session_employee_id(token_hash: str) -> int | None:
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT employee_id FROM sessions WHERE token_hash = %s AND expires_at > now()", (token_hash,)
        ).fetchone()
    return row[0] if row else None


def delete_session(token_hash: str) -> None:
    with get_pool().connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash,))


# ---- admin: questions log, audit log, policies, employee writes -----------------
def log_qa(employee_id: int, conv: str, question: str, answered: bool, reason: str, top_score: float | None) -> None:
    with get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO qa_log (employee_id, conversation_id, question, answered, reason, top_score) VALUES (%s,%s,%s,%s,%s,%s)",
            (employee_id, conv, question, answered, reason, top_score),
        )


_QA_KIND = {
    "unanswered": "q.answered = false AND q.reason <> 'access_denied'",
    "denied": "q.reason = 'access_denied'",
    "all": "q.reason <> 'smalltalk'",
}


def qa_list(kind: str, limit: int) -> list[dict]:
    with get_pool().connection() as conn:
        cur = conn.execute(
            f"""SELECT q.id, q.question, q.answered, q.reason, q.top_score, q.created_at, e.department
                FROM qa_log q LEFT JOIN employees e ON e.id = q.employee_id
                WHERE {_QA_KIND[kind]} ORDER BY q.id DESC LIMIT %s""", (limit,))
        return _rows(cur)


def qa_stats(days: int) -> dict:
    with get_pool().connection() as conn:
        per_day = _rows(conn.execute(
            """SELECT created_at::date AS day,
                      count(*) FILTER (WHERE answered) AS answered,
                      count(*) FILTER (WHERE NOT answered AND reason <> 'access_denied') AS unanswered,
                      count(*) FILTER (WHERE reason = 'access_denied') AS denied
               FROM qa_log WHERE reason <> 'smalltalk' AND created_at > now() - make_interval(days => %s)
               GROUP BY 1 ORDER BY 1""", (days,)))
        top = _rows(conn.execute(
            """SELECT lower(question) AS question, count(*) AS times FROM qa_log
               WHERE NOT answered AND reason <> 'access_denied' AND created_at > now() - make_interval(days => %s)
               GROUP BY 1 ORDER BY times DESC, max(id) DESC LIMIT 10""", (days,)))
    return {"per_day": per_day, "top_unanswered": top}


def audit(actor: dict, action: str, target: str = "", detail: str = "") -> None:
    with get_pool().connection() as conn:
        conn.execute("INSERT INTO audit_log (actor_id, actor_name, action, target, detail) VALUES (%s,%s,%s,%s,%s)",
                     (actor["id"], actor["full_name"], action, target, detail))


def audit_list(limit: int) -> list[dict]:
    with get_pool().connection() as conn:
        return _rows(conn.execute(
            "SELECT id, actor_name, action, target, detail, created_at FROM audit_log ORDER BY id DESC LIMIT %s", (limit,)))


def policies_list() -> list[dict]:
    with get_pool().connection() as conn:
        return _rows(conn.execute(
            """SELECT source_file, min(doc_name) AS doc_name, min(policy_id) AS policy_id, min(version) AS version,
                      count(*) AS chunks, max(ingested_at) AS ingested_at
               FROM policy_chunks GROUP BY source_file ORDER BY source_file"""))


def delete_document(source_file: str) -> int:
    with get_pool().connection() as conn:
        return conn.execute("DELETE FROM policy_chunks WHERE source_file = %s", (source_file,)).rowcount


UPDATABLE = {"full_name", "title", "department", "grade", "employment_status", "access_role", "work_location",
             "work_model", "manager_id", "join_date", "confirmation_date", "end_date", "grade_since", "gross_salary",
             "last_rating", "on_pip", "annual_used", "casual_used", "sick_used", "covered_dependents", "pf_enrolled"}
INSERTABLE = UPDATABLE | {"emp_code", "email", "password_hash", "last_increment_pct", "hajj_taken"}


def update_employee(emp_id: int, fields: dict) -> None:
    assert fields and set(fields) <= UPDATABLE
    sets = ", ".join(f"{k} = %s" for k in fields)
    with get_pool().connection() as conn:
        conn.execute(f"UPDATE employees SET {sets} WHERE id = %s", [*fields.values(), emp_id])


def insert_employee(values: dict) -> int:
    assert set(values) <= INSERTABLE
    cols = ", ".join(values)
    with get_pool().connection() as conn:
        return conn.execute(f"INSERT INTO employees ({cols}) VALUES ({', '.join(['%s'] * len(values))}) RETURNING id",
                            list(values.values())).fetchone()[0]


def set_password(emp_id: int, password_hash: str) -> None:
    with get_pool().connection() as conn:
        conn.execute("UPDATE employees SET password_hash = %s WHERE id = %s", (password_hash, emp_id))
        conn.execute("DELETE FROM sessions WHERE employee_id = %s", (emp_id,))


def delete_employee(emp_id: int) -> None:
    with get_pool().connection() as conn:
        conn.execute("DELETE FROM employees WHERE id = %s", (emp_id,))


def next_emp_number() -> int:
    with get_pool().connection() as conn:
        return conn.execute("SELECT coalesce(max(substring(emp_code from 5)::int), 0) + 1 FROM employees").fetchone()[0]


# ---- admin accounts (separate from employee accounts) ---------------------------
def count_admins() -> int:
    with get_pool().connection() as conn:
        return conn.execute("SELECT count(*) FROM admins").fetchone()[0]


def create_admin(username: str, full_name: str, password_hash: str, level: str) -> int:
    with get_pool().connection() as conn:
        return conn.execute("INSERT INTO admins (username, full_name, password_hash, level) VALUES (lower(%s),%s,%s,%s) RETURNING id",
                            (username, full_name, password_hash, level)).fetchone()[0]


def get_admin_login(username: str) -> tuple[int, str] | None:
    with get_pool().connection() as conn:
        row = conn.execute("SELECT id, password_hash FROM admins WHERE username = lower(%s)", (username.strip(),)).fetchone()
    return (row[0], row[1]) if row else None


def get_admin(admin_id: int) -> dict | None:
    with get_pool().connection() as conn:
        rows = _rows(conn.execute("SELECT id, username, full_name, level FROM admins WHERE id = %s", (admin_id,)))
    return rows[0] if rows else None


def list_admins() -> list[dict]:
    with get_pool().connection() as conn:
        return _rows(conn.execute("SELECT id, username, full_name, level FROM admins ORDER BY id"))


def set_admin_password(username: str, password_hash: str) -> int:
    with get_pool().connection() as conn:
        n = conn.execute("UPDATE admins SET password_hash = %s WHERE username = lower(%s)", (password_hash, username)).rowcount
        conn.execute("DELETE FROM admin_sessions WHERE admin_id IN (SELECT id FROM admins WHERE username = lower(%s))", (username,))
    return n


def delete_admin(username: str) -> int:
    with get_pool().connection() as conn:
        return conn.execute("DELETE FROM admins WHERE username = lower(%s)", (username,)).rowcount


def create_admin_session(token_hash: str, admin_id: int, hours: int) -> None:
    with get_pool().connection() as conn:
        conn.execute("DELETE FROM admin_sessions WHERE expires_at < now()")
        conn.execute("INSERT INTO admin_sessions (token_hash, admin_id, expires_at) VALUES (%s,%s, now() + make_interval(hours => %s))",
                     (token_hash, admin_id, hours))


def admin_session_id(token_hash: str) -> int | None:
    with get_pool().connection() as conn:
        row = conn.execute("SELECT admin_id FROM admin_sessions WHERE token_hash = %s AND expires_at > now()", (token_hash,)).fetchone()
    return row[0] if row else None


def delete_admin_session(token_hash: str) -> None:
    with get_pool().connection() as conn:
        conn.execute("DELETE FROM admin_sessions WHERE token_hash = %s", (token_hash,))
