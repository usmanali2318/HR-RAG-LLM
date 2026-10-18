---
title: Zakheera HR Assistant
emoji: 🌿
colorFrom: green
colorTo: yellow
sdk: docker
app_port: 8000
pinned: false
---

# ZAKHEERA HR Assistant (RAG)

Zakheera is a fictional AI company and the name of my FYP project (this is not my fyp).
In order to get a grasp of RAG system essential for our main FYP I decided to build this portfolio project. This system works as HR Assistant which answers Frequently ask questions from the available policy documents and refuses when it does not know or the employee asking has no permission.
Apart from getting information from policy documents it also takes into account employees rank, role, service, pay, status etc to give a more personalized answer.

```
Browser chat  ->  FastAPI /api/chat  ->  1. rewrite follow-up into a standalone question (Gemini)
                                         2. embed question (Gemini embeddings)
                                         3. vector search in Postgres + pgvector (hosted)
                                         4. gate: best score below MIN_SCORE -> refuse (no LLM call)
                                         5. grounded answer as JSON: sufficient_context + cited excerpts (Gemini)
                                         6. validate: not sufficient, or no valid citation -> refuse
                                         7. answer + sources + follow-up suggestions
```

## Try the demo

**Live demo:** https://hr-rag-llm-7e297.containers.snapdeploy.app/login OR **Admin Panel:** https://hr-rag-llm-7e297.containers.snapdeploy.app/admin/login

Everything here is fictional: the company, the 50 employees and their records. Log in with any account below.
| Role | Email | Password | What to try |
|---|---|---|---|
| CEO (full access, admin panel) | `usman.ali@zakheera.ai` | `zakheera@usman` | "Who is on a PIP?", open the Admin panel from the avatar menu |
| Head of HR (HR access, admin panel) | `sadia.rahman@zakheera.ai` | `zakheera@sadia` | "How many employees are in each department?" |
| Engineering manager | `salman.ghani@zakheera.ai` | `zakheera@salman` | "Who in my team is on probation?" |
| Senior engineer | `ahmed.raza@zakheera.ai` | `zakheera@ahmed` | "How many leaves do I have left?", "Am I eligible for gratuity?" |
| On probation | `rabia.anwar@zakheera.ai` | `zakheera@rabia` | "Can I work from home?" |
| Intern | `saad.yousuf@zakheera.ai` | `zakheera@saad` | "What is my notice period?" |

All 50 logins are in [`data/credentials.csv`](data/credentials.csv). The pattern is `firstname.lastname@zakheera.ai` and `zakheera@firstname`.

For starters if you ask regarding other employees' salary as a non managerial role, the assistant will refuse to answer even with a manegerial role you can ask about employees under your own team and no one outside it, the full assistant access is granted to HR heads and CEO.

If the demo has broken data, running `python -m app.seed --reset` restores it.

## 1. Set up the cloud pieces (about 10 minutes)

1. **Gemini key:** https://aistudio.google.com/apikey, copy the key.
2. **Database:** create a free project on Supabase or Neon. Copy the Postgres connection string (include `sslmode=require`). pgvector is available on both; the app runs `CREATE EXTENSION vector` itself.
   On Supabase, the "session pooler" or direct connection string works best from a long-running server.
3. `cp .env.example .env` and fill in `GEMINI_API_KEY` and `DATABASE_URL`.

## 2. Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m app.ingest data/policies          # parse PDFs, embed, store (about 200 chunks)
uvicorn app.main:app --reload               # open http://localhost:8000
```

Only your laptop runs the web server here. The database and the LLM are both in the cloud.

Re-running `ingest` skips unchanged PDFs. Use `--force` to re-embed everything, `--reset` to drop and recreate the table (required if you change `EMBED_DIM`), `--dry-run` to preview chunks without any API calls.

## 2b. Load the 50 dummy employees and their logins

```bash
python -m app.seed             # creates the tables and loads employees (skips if already loaded)
python -m app.seed --reset     # wipe and reload employees, sessions and chat history
```
This writes `data/credentials.csv` with every employee's email and password (dummy, for testing). Emails are `firstname.lastname@zakheera.ai` (the CEO, Syed Usman Ali, is `usman.ali@zakheera.ai`). Passwords are `zakheera@firstname`, for example `zakheera@usman`. If two employees share a first name, each gets their employee id added, such as `zakheera@ahmed0012`. Only salted PBKDF2 hashes are stored in the database. The login page also has a "Try a demo account" list with six one-click accounts.

Who can see what (enforced in `app/people.py` before the prompt is built, so the model never receives data the user may not see):

| Access | Sees |
|---|---|
| Employee | Own full record, plus directory entries (name, title, department, location, email, manager) of people they name |
| Manager | The above, plus everyone below them in the reporting line (leave, status, rating, PIP), but no pay or benefits |
| HR (4 HR staff, not the HR intern) | Everyone, including pay and benefits |
| Executive (CEO) | Everyone, including pay and benefits |

## 2c. Admin panel (HR and CEO only)

One account system: the CEO and the four HR employees open the admin panel with their normal work login (email and password). Signing in to the chat or to `/admin` signs you in to both, and **Log out** in the admin panel signs you out of both. Other employees, including managers and the HR intern, cannot open it.



| Tab | What you can do |
|---|---|
| Overview | Employee and policy counts, questions per day, answer rate, the most asked questions the assistant could not answer |
| Employees | Search, edit any field, add employees (login and starting password are generated), reset passwords (ends their sessions) |
| Policies | Upload a policy PDF (same file name replaces the old version and is re-indexed), remove a policy |
| Questions | What employees asked: could not answer, not allowed, all. Only the asker's department is shown, never their name |
| Activity | Audit log of every admin change. Salary values are never logged |

Rules: the CEO counts as `super`; HR employees count as `hr`. An `hr` admin can do all of the above except change access levels, edit the CEO's record, or delete employees. Those need a `super` admin.

## 3. Calibrate the "I don't know" threshold (do this once)

```bash
python -m scripts.eval            # prints the top similarity score for 34 test questions and suggests MIN_SCORE
python -m scripts.eval --full     # runs the whole pipeline and checks answers and refusals
```

Put the suggested value in `.env` as `MIN_SCORE`. The default 0.55 is a starting guess, not a measured value. Add your own questions to `tests/eval_questions.json` over time.

## 4. Deploy (also non-local)

The repo has a `Dockerfile`. On Render, Railway or Fly.io: create a web service from the repo, set the environment variables from `.env.example` (never commit `.env`), and deploy. Check `https://YOUR-APP/api/health`, which reports database status and chunk count.

## Project layout

| Path | Purpose |
|---|---|
| `app/ingest.py` | PDF parsing (headings by font size, tables turned into readable rows, FAQ pairs kept whole), chunking, embedding, storing |
| `app/db.py` | Schema, vector search, employees, sessions, chat history |
| `app/people.py` | Access rules, leave/gratuity/provident fund calculations, builds the data the model may see |
| `app/auth.py` | Password hashing, login sessions (HttpOnly cookie), brute-force lockout |
| `app/seed.py`, `app/seed_data.py` | The 50 dummy employees and their credentials |
| `app/embeddings.py` | Gemini embeddings (document vs query task types, normalised) |
| `app/llm.py` | System prompt, question rewriting, grounded structured answer |
| `app/rag.py` | The pipeline and the three layers of refusal |
| `app/main.py` | FastAPI: `/api/chat`, `/api/history/{id}`, `/api/health`, serves the UI |
| `static/login.html`, `static/index.html`, `static/style.css` | Login page and chat UI (mobile responsive, ivory and green palette) |
| `app/admin.py`, `static/admin.html` | Admin API and page |
| `app/admin_users.py` | Command line to manage admin accounts |
| `tests/` | 61 automated tests (fake embedder and fake LLM, real Postgres) and the evaluation question set |

## Tests

```bash
pip install -r requirements-dev.txt
TEST_DATABASE_URL=postgresql://user:pw@localhost:5432/testdb python -m pytest -q tests
```
Needs any Postgres with pgvector. No Gemini key needed, the tests replace Gemini with fakes.


