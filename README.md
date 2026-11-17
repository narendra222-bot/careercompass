# CareerCompass

**SIH25094** · An AI agent that turns a short chat into a week-by-week career roadmap, grounded in
O*NET 31.0 job data.

A student describes themselves in their own words (branch, year, target job, weekly hours, what
they already know). CareerCompass asks only for what is missing, matches the job to O*NET
occupations, finds the skill gaps, and builds a phased plan of courses and projects with a job
readiness score. Every course link is checked before it is shown.

- **Backend:** Python, FastAPI, LangGraph, DuckDB (O*NET), ChromaDB + BM25 search, Groq LLMs
- **Frontend:** Next.js 16, React 19 (`web/`)

Team: Guvvala Venkata Narendra, Priyanshu Singh, Jasleen Kaur (LPU, CSE473).

**Live demo:** https://careercompass-five-omega.vercel.app · API: https://careercompass-api-zeta.vercel.app/api/health

---

## 1. What you need

| Tool | Version | Check |
|---|---|---|
| Python | **3.12** (3.11 also works; 3.13+ is not supported by some libraries) | `python --version` |
| Node.js | 20 or newer | `node --version` |
| pnpm | 9 or newer | `pnpm --version` (install: `npm install -g pnpm`) |
| Git | any | `git --version` |

About **3 GB** of free disk space (PyTorch and the O*NET data).

## 2. Get the code

```bash
git clone https://github.com/narendra222-bot/careercompass.git
cd careercompass
```

## 3. Backend setup

**Linux / macOS**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

**Windows (PowerShell)**

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
```

The install downloads PyTorch, so it can take 5 to 10 minutes.

### Environment file

Copy the example and fill it in:

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

| Variable | Needed? | What it is |
|---|---|---|
| `GROQ_API_KEY` | Optional | Free key from https://console.groq.com/keys. Without it the app still works with rule-based replies and the 3 built-in roles (DevOps Engineer, Data Analyst, Machine Learning Engineer). With it, the agent understands free text better and can plan any job role. |
| `APP_SECRET` | Recommended | Any random text of 32+ characters. Signs login sessions; without it everyone is logged out when the server restarts. Generate one: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `GOOGLE_CLIENT_ID` | Optional | Google OAuth client id for "Continue with Google". Without it only email login is shown. |
| `ALLOW_EMAIL_LOGIN` | Optional | `true` (default) or `false` |
| `CORS_ORIGINS` | Optional | Where the web app runs. Default `http://localhost:3000` |

**Never commit `.env`** and never share your Groq key in chat or screenshots.

### Build the O*NET data (one time)

The O*NET database and search index are not in Git (they are large). This downloads O*NET 31.0
(about 13 MB) and builds them (about 3 minutes):

```bash
python scripts/build_data.py
```

You should see `occupation : 1,016` and `Indexed 1016 occupations. Data is ready.`

### Run the backend

```bash
uvicorn careercompass.api:app --port 8000
```

Check it: open http://localhost:8000/api/health. You should see `"status":"ok"` and
`"occupations":1016`. API docs: http://localhost:8000/docs

## 4. Frontend setup

In a **second terminal**:

```bash
cd web
cp .env.example .env.local   # Windows: copy .env.example .env.local
pnpm install
pnpm dev
```

Open **http://localhost:3000**, click **Try CareerCompass**, sign in with a name and email, and
type something like:

> I'm in 3rd year B.Tech CSE, I can study 10 hours a week and I want to become a DevOps engineer.
> I know Linux and Git.

For a production build use `pnpm build` and then `pnpm start`.

## 5. Tests

```bash
python -m pytest -q tests        # about 4 minutes, all tests should pass
ruff check careercompass
```

## Deploying to Vercel

Both parts run on Vercel as two projects.

**Backend** (`careercompass-api`): a Python function built from a staged folder that holds the
code, the prebuilt O*NET database and search index, and the embedding model. It uses
`fastembed` (ONNX) instead of PyTorch, which gives the same vectors in a much smaller bundle.

```bash
make build-data                                # once, so data/onet.duckdb and data/chroma exist
deploy/vercel-api/stage.sh /tmp/cc-api         # stage the function
cd /tmp/cc-api && vercel link --project careercompass-api && vercel deploy --prod
```

Environment variables on the backend project:

| Variable | Value |
|---|---|
| `GROQ_API_KEY`, `APP_SECRET` | as in `.env` (mark them sensitive) |
| `CORS_ORIGINS` | the frontend URL |
| `DATABASE_URL` | Postgres for accounts and chats (Neon from the Vercel Marketplace). Without it accounts live in `/tmp` and reset when the function restarts |
| `VERCEL_SUPPORT_LARGE_FUNCTIONS` | `1` (the bundle is a little over 500 MB) |

**Frontend** (`careercompass`): deploy the `web/` folder with
`NEXT_PUBLIC_API_URL` set to the backend URL.

## Make shortcuts (Linux / macOS)

| Command | Does |
|---|---|
| `make setup` | Create `.venv` and install Python packages |
| `make build-data` | Download O*NET and build the database and search index |
| `make api` | Run the backend on port 8000 (auto-reload) |
| `make web-setup` / `make web` | Install and run the web app on port 3000 |
| `make test` | Run unit tests |
| `make demo` | Regenerate the 3 sample roadmaps and JSON schema in `docs/` |

## Project layout

```
careercompass/      backend
  agent.py          conversation: understand the chat, decide, reply
  chat.py           /api/chat streaming logic
  graph.py          LangGraph flow: intake -> plan -> validate -> adjust -> explain
  planner.py        planner for the 3 curated roles
  custom_planner.py planner for any other role (O*NET + LLM)
  research.py       finds and verifies course links
  scoring.py        job readiness formulas
  retrieval.py      hybrid BM25 + vector search over O*NET
  db.py             O*NET DuckDB build and queries
  accounts.py       users, sessions, saved chats (SQLite)
  api.py            FastAPI routes
data/               catalog, role profiles, aliases (O*NET files are built locally)
docs/               sample roadmaps, roadmap JSON schema, evaluation results
scripts/            build_data.py and other helper scripts
tests/              unit and property tests
web/                Next.js web app
```

## Common problems

| Problem | Fix |
|---|---|
| `/api/health` fails or shows 0 occupations | Run `python scripts/build_data.py` from the project root |
| Web page says it can't reach the server | Start the backend first; check `NEXT_PUBLIC_API_URL` in `web/.env.local` |
| Login works but chat fails with a CORS error | Set `CORS_ORIGINS` in `.env` to the URL the web app runs on |
| Replies are simple and only 3 roles work | Add `GROQ_API_KEY` to `.env`, or the Groq free quota is used up (it refills within 24 hours) |
| `pip install` fails on Python 3.13 / 3.14 | Use Python 3.12 |

## Data licence

O*NET 31.0 is from the U.S. Department of Labor, used under the
[CC BY 4.0 licence](https://www.onetcenter.org/license_db.html).
