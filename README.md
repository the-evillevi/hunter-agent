# hunter-agent

A small FastAPI, SQLite, SQLModel, and HTMX project for learning by building a
job tracker.

## Quest Roadmap

The project roadmap lives in the Obsidian vault at `vault/`. Open
`vault/HNTR Quests.base` to see the canonical quest list.

Work through quests in `order` sequence. The application exercises in
`tests/test_applications.py` are skipped initially so the existing test suite
stays green. Remove a test's `@pytest.mark.skip` decorator when you start its
related quest.

## Test Practice

Run the complete suite:

```sh
uv run pytest -q
```

Run only the application exercises:

```sh
uv run pytest tests/test_applications.py -q
```

To start one skipped exercise, remove its `@pytest.mark.skip` decorator and run
that test by name. The failure message should point at the next implementation
step.

> Automated job application agent · MacBook Pro M5 16GB · Ollama · Python · SQLite

---

## Overview

Hunter Agent is a fully automated job application pipeline that crawls multiple job boards, scores listings against your CV using a layered AI stack, tailors your CV per application, submits applications autonomously, and tracks every outcome in a local SQLite database with a web dashboard.

**Core rules:**

1. Never apply blindly — every application is scored first
2. Never duplicate — SQLite deduplicates across every run
3. Never break sources carelessly — legitimate data paths first, scraping as fallback

---

## Stack

| Layer              | Tool                          | Why                                           |
| ------------------ | ----------------------------- | --------------------------------------------- |
| Language           | Python 3.12+                  | Best ecosystem for scraping + AI + automation |
| Package manager    | uv                            | Fast, modern, replaces pip+venv               |
| Scheduling         | APScheduler                   | Runs inside the app, no cron needed           |
| Browser automation | Playwright                    | Handles JS-heavy sites                        |
| Local AI           | Ollama + ollama Python lib    | Free, private, Metal-accelerated on M5        |
| Storage            | SQLite + SQLModel             | Zero-infra, file-based, queryable             |
| CV generation      | python-docx + WeasyPrint      | Edit Word master → export PDF                 |
| Dashboard          | FastAPI + HTMX + TailwindCSS  | Lightweight, no JS framework needed           |
| Semantic scoring   | sentence-transformers (SBERT) | Local, 80MB, catches synonyms                 |
| Keyword scoring    | rank-bm25                     | Deterministic, fast, no model needed          |
| Config             | TOML                          | Human-readable, easy to tune                  |

### Model strategy for 16GB M5

macOS uses ~4–6GB, leaving ~10–12GB for models. The pipeline is sequential — models are never loaded simultaneously.

| Task         | Model       | Size   | Notes                                              |
| ------------ | ----------- | ------ | -------------------------------------------------- |
| Scoring      | qwen2.5:7b  | ~4.7GB | Fast — runs on hundreds of jobs per cycle          |
| CV tailoring | qwen2.5:14b | ~8.7GB | Better writing — only runs on jobs above threshold |

---

## Architecture

```
┌─────────────────────────────────────────────┐
│           APScheduler (8am + 6pm)            │
└────────────────┬────────────────────────────┘
                 │
       ┌─────────▼──────────┐
       │    Job Crawler      │  Adzuna API · Remotive API · Decodo · LinkedIn
       │  (swappable sources)│
       └─────────┬──────────┘
                 │ raw listings
       ┌─────────▼──────────┐
       │  Dedup + Filter     │  hash(company+title+location) → skip if seen
       └─────────┬──────────┘
                 │ new jobs only
       ┌─────────▼──────────┐
       │  Scoring Pipeline   │  1. Hard filter (instant)
       │                     │  2. BM25 + Jaccard keyword score
       │                     │  3. SBERT semantic score
       │                     │  4. O*NET/ESCO ontology bonus (optional)
       │                     │  5. LLM score (gated — only if 2+3 ≥ 45)
       └─────────┬──────────┘
                 │ jobs above threshold (e.g. 78%)
       ┌─────────▼──────────┐
       │    CV Tailor        │  qwen2.5:14b rewrites summary + bullets → PDF
       └─────────┬──────────┘
                 │ tailored CV ready
       ┌─────────▼──────────┐
       │  Application Engine │  Playwright Easy Apply · email handler
       └─────────┬──────────┘
                 │
       ┌─────────▼──────────┐
       │     SQLite DB       │  everything logged
       └─────────┬──────────┘
                 │
       ┌─────────▼──────────┐
       │  FastAPI Dashboard  │  track · override · blacklist
       │  localhost:8000     │
       └─────────────────────┘
```

---

## Project Layout

```
hunter-agent/
├── config.toml              ← credentials + tuning (no toggles — those live in DB)
├── form_defaults.toml       ← standard form answers (name, salary, right to work…)
├── hunter-agent.db          ← SQLite database
├── cv/
│   ├── master.docx          ← your master CV
│   └── tailored/            ← company_role_YYYYMMDD.pdf outputs
├── sources/
│   ├── base.py              ← JobSource ABC
│   ├── registry.py          ← DB + config join → active sources
│   ├── adzuna.py
│   ├── remotive.py
│   ├── decodo.py
│   └── linkedin.py
├── llm/
│   ├── base.py              ← LLMProvider ABC
│   ├── registry.py          ← task router + fallback chains
│   ├── ollama.py
│   ├── anthropic.py
│   └── openai.py
├── scoring/
│   ├── base.py              ← Scorer ABC
│   ├── registry.py          ← pipeline orchestrator
│   ├── keyword.py           ← BM25 + Jaccard
│   ├── semantic.py          ← SBERT cosine similarity
│   ├── ontology.py          ← O*NET / ESCO (Phase 2+)
│   ├── llm.py               ← LLM scorer via llm.registry
│   └── pipeline.py          ← weighted blend + gating logic
├── cv/
│   ├── parser.py            ← master.docx → structured JSON
│   └── tailor.py            ← JSON + job → tailored .docx → PDF
├── application/
│   ├── form_handler.py      ← Playwright Easy Apply
│   └── email_handler.py     ← SMTP apply
├── dashboard/
│   ├── main.py              ← FastAPI app
│   └── templates/           ← HTMX templates
└── scheduler.py             ← APScheduler entry point
```

---

## Database Schema

```sql
-- Toggleable job sources (enabled lives here, not config.toml)
CREATE TABLE sources (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    enabled    BOOLEAN DEFAULT true,
    added_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Lookup tables (normalised)
CREATE TABLE companies (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE locations (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE keywords (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

-- Your 2–3 target job profiles
CREATE TABLE profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    role_name       TEXT,
    salary_min      INT,
    location_type   TEXT CHECK(location_type IN ('remote', 'hybrid', 'onsite')),
    match_threshold INT CHECK(match_threshold BETWEEN 1 AND 100),
    active          BOOLEAN
);

CREATE TABLE profile_keywords (
    profile_id INTEGER NOT NULL REFERENCES profiles(id),
    keyword_id INTEGER NOT NULL REFERENCES keywords(id),
    PRIMARY KEY (profile_id, keyword_id)
);

-- Every job ever seen (dedup anchor)
CREATE TABLE jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      INTEGER NOT NULL REFERENCES profiles(id),
    title           TEXT,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    location_id     INTEGER NOT NULL REFERENCES locations(id),
    url             TEXT UNIQUE,
    source_id       INTEGER NOT NULL REFERENCES sources(id),
    description     TEXT,
    hash            TEXT UNIQUE,        -- hash(company+title+location)
    scraped_at      DATETIME,
    -- Scoring (all layers stored for MLOps comparison)
    keyword_score   INT,
    semantic_score  INT,
    llm_score       INT,
    score           INT CHECK(score BETWEEN 1 AND 100),   -- final blended
    score_reasoning TEXT
);

-- Every application attempt
CREATE TABLE applications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       INT,
    cv_path      TEXT,
    status       TEXT CHECK(status IN (
                   'pending', 'draft', 'applied', 'acknowledged',
                   'interviews', 'rejected', 'ghosted', 'offer', 'accepted'
                 )),
    applied_at   DATETIME,
    last_updated DATETIME,
    notes        TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

-- Blacklist: a whole company OR a specific job (never both)
CREATE TABLE blacklist (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER REFERENCES companies(id),
    job_id     INTEGER REFERENCES jobs(id),
    reason     TEXT,
    added_at   DATETIME,
    CHECK (
        (company_id IS NOT NULL AND job_id IS NULL)
        OR
        (company_id IS NULL AND job_id IS NOT NULL)
    )
);
```

> **Note:** `scraped_at`, `applied_at`, `last_updated` should be `DATETIME` (not `DATE`) to preserve time precision.

---

## config.toml

```toml
# ============================================================
# hunter-agent — master configuration
# credentials + tuning only — toggles live in the DB
# ============================================================

[agent]
name      = "hunter-agent"
version   = "0.1.0"
db_path   = "./hunter-agent.db"
cv_master = "./cv/master.docx"
cv_output = "./cv/tailored/"
log_level = "INFO"

# ============================================================
# SCHEDULER
# ============================================================

[scheduler]
enabled   = true
runs_at   = ["08:00", "18:00"]
timezone  = "America/Mexico_City"
lock_file = "/tmp/hunter-agent.lock"

# ============================================================
# LOCAL AI — OLLAMA
# ============================================================

[llm.providers.ollama]
base_url = "http://localhost:11434"

[llm.providers.anthropic]
api_key = ""   # or set ANTHROPIC_API_KEY env var

[llm.providers.openai]
api_key  = ""  # or set OPENAI_API_KEY env var
base_url = "https://api.openai.com/v1"

# Task → model routing (change 2 lines to swap the whole AI brain)
[llm.tasks.scorer]
provider    = "ollama"
model       = "qwen2.5:7b"
temperature = 0.1
max_tokens  = 512

[llm.tasks.tailor]
provider    = "ollama"
model       = "qwen2.5:14b"
temperature = 0.3
max_tokens  = 2048

# Fallback chains (if primary provider is unreachable)
[llm.fallback]
enabled = true

  [llm.fallback.scorer]
  chain = ["ollama/qwen2.5:7b", "anthropic/claude-haiku-4-5-20251001"]

  [llm.fallback.tailor]
  chain = ["ollama/qwen2.5:14b", "ollama/qwen2.5:7b", "anthropic/claude-sonnet-4-6"]

# ============================================================
# SCORING PIPELINE
# ============================================================

[scoring.keyword]
enabled = true
weight  = 0.25

[scoring.semantic]
enabled = true
model   = "all-MiniLM-L6-v2"   # 80MB, downloads once via sentence-transformers
weight  = 0.35

[scoring.llm]
enabled       = true
weight        = 0.40
min_pre_score = 45   # only run LLM if keyword+semantic blended >= 45

[scoring.ontology]
enabled = false      # enable after core pipeline is stable
source  = "onet"     # onet | esco

# ============================================================
# JOB SOURCES — credentials + tuning only
# enabled toggle lives in DB sources table
# ============================================================

[sources.adzuna]
app_id           = "YOUR_ADZUNA_APP_ID"
app_key          = "YOUR_ADZUNA_APP_KEY"
country          = "us"
results_per_page = 50
max_pages        = 5

[sources.remotive]
# no credentials needed

[sources.decodo]
api_key     = "YOUR_DECODO_KEY"
targets     = ["linkedin.com/jobs", "glassdoor.com/Jobs"]
render_js   = true
timeout_s   = 30
max_retries = 3

[sources.linkedin]
# Uses Playwright with a logged-in session cookie
session_cookie = ""   # li_at cookie value from browser DevTools

# ============================================================
# NOTIFICATIONS
# ============================================================

[notifications.macos]
enabled = true   # osascript — no setup needed

[notifications.email]
enabled      = false
smtp_host    = "smtp.gmail.com"
smtp_port    = 587
from         = "you@gmail.com"
to           = "you@gmail.com"
app_password = ""

# ============================================================
# DASHBOARD
# ============================================================

[dashboard]
host              = "127.0.0.1"
port              = 8000
auto_open_browser = true
```

---

## Swappable Protocols

All three major layers — sources, LLMs, and scorers — use the same **Protocol pattern**: an abstract base class defines the contract, a registry discovers and instantiates implementations from config, and the pipeline never references a concrete class directly.

### Adding a new source

1. Create `sources/newsource.py` implementing `JobSource`
2. Add `[sources.newsource]` block to `config.toml` with credentials
3. `INSERT INTO sources (name) VALUES ('newsource')`
4. Zero pipeline changes required

### Switching LLM models

Edit two lines in `config.toml`:

```toml
[llm.tasks.scorer]
provider = "anthropic"
model    = "claude-haiku-4-5-20251001"
```

### LLM provider matrix

| Scenario                | scorer                                                | tailor                  |
| ----------------------- | ----------------------------------------------------- | ----------------------- |
| Daily running (default) | ollama/qwen2.5:7b                                     | ollama/qwen2.5:14b      |
| Testing Gemma 4         | ollama/gemma4:12b                                     | ollama/qwen2.5:14b      |
| Best quality run        | anthropic/claude-haiku                                | anthropic/claude-sonnet |
| Mac offline             | ollama/llama3.2:3b                                    | ollama/qwen2.5:7b       |
| Benchmarking            | run same jobs through all providers, compare outcomes |                         |

---

## Scoring Pipeline Detail

### Layer stack

| #   | Layer          | Tool                     | Cost            | Notes                                       |
| --- | -------------- | ------------------------ | --------------- | ------------------------------------------- |
| 1   | Hard filter    | Rule-based               | Free, instant   | Location, salary, exclude_keywords          |
| 2   | Keyword score  | BM25 + Jaccard           | Free, fast      | Exact skill overlap, deterministic          |
| 3   | Semantic score | SBERT (all-MiniLM-L6-v2) | Free, local     | Catches synonyms and related skills         |
| 4   | Ontology bonus | O\*NET / ESCO            | Free, optional  | Rewards transferable skills via skill graph |
| 5   | LLM score      | qwen2.5:7b               | Local inference | Gated: only runs if layers 2+3 ≥ 45         |

**Final score = 0.25 × keyword + 0.35 × semantic + 0.40 × llm**

All individual layer scores are stored in the DB alongside the final score — enabling MLOps comparison over time.

### Why deterministic layers matter

Having BM25 and SBERT scores running in parallel to the LLM lets you answer:

- When keyword_score and llm_score disagree by >20 points, which predicted outcomes better?
- Does SBERT catch things BM25 misses for AI Engineer roles specifically?
- Is the LLM systematically over-scoring certain companies (sycophancy)?
- Can the LLM layer be dropped entirely for low-stakes roles?

---

## Build Phases

### Phase 0 — Environment setup ✅

- uv init job-agent && cd job-agent
- Install Ollama from ollama.com (runs as background service on Mac)
- `ollama pull qwen2.5:7b` and `ollama pull qwen2.5:14b`
- Verify Metal acceleration: `ollama run qwen2.5:7b`
- Create hunter-agent.db with full schema
- Install core dependencies via uv

### Phase 1 — Foundation (~1 day)

- SQLModel models wrapping the DB schema
- config.toml with full structure (credentials + tuning, no toggles)
- CV parser: master.docx → structured JSON (skills, experience, education, summary)
- Bare FastAPI app + /dashboard route

### Phase 2 — Crawler layer (~2 days)

- `sources/base.py` — JobSource ABC: `fetch(profile)`, `health_check()`
- `sources/registry.py` — JOIN sources WHERE enabled=true × config keys → instantiate
- `sources/adzuna.py` — free REST API, best starting point
- `sources/remotive.py` — free public API, remote-first, zero credentials
- `sources/decodo.py` — residential proxies + JS rendering, enables LinkedIn/Glassdoor
- Deduplication: hash(company + title + location), skip if in DB
- Randomized 2–8s delays between requests

> ⚠️ Start with Adzuna + Remotive. Enable Decodo/LinkedIn only after those are stable.

### Phase 3 — LLM layer (~1.5 days)

- `llm/base.py` — LLMProvider ABC: `complete(system, user, temp, max_tokens) → LLMResponse`
- `llm/registry.py` — task router + fallback chain logic
- `llm/ollama.py` — Qwen, Llama, Gemma 4, any Ollama model
- `llm/anthropic.py` — Claude Haiku / Sonnet via API
- `llm/openai.py` — GPT-4o, o3, Codex (base_url swappable)

### Phase 4 — Scoring pipeline (~2 days)

- `scoring/base.py` — Scorer ABC
- `scoring/keyword.py` — BM25 + Jaccard skill overlap
- `scoring/semantic.py` — SBERT cosine similarity (all-MiniLM-L6-v2)
- `scoring/llm.py` — calls llm.registry, returns 0–100 + reasoning JSON
- `scoring/pipeline.py` — hard filter → keyword → semantic → LLM gate → blend
- Store all layer scores individually in DB for later analysis
- Prompt injection guard: sanitize job descriptions before LLM ingestion

### Phase 5 — CV tailoring (~2 days)

- Unload qwen2.5:7b, load qwen2.5:14b (sequential — respects 16GB limit)
- Rewrites: professional summary · top bullet points per role · skill order
- Never invents facts — restructures and reemphasises existing content only
- Output: modified copy of master.docx → exported PDF via WeasyPrint
- Saved to `/cv/tailored/company_role_YYYYMMDD.pdf`

> ⚠️ Manually review the first 20 tailored CVs before enabling full auto-apply.

### Phase 6 — Application engine (~3 days, hardest phase)

- `form_defaults.toml` — name, email, phone, years XP, salary expectation, right to work, etc.
- Easy Apply handler — Playwright fills LinkedIn / Indeed Quick Apply forms
- Email handler — detects `apply@company.com` patterns, sends PDF via SMTP
- On success → status = `applied`, timestamp logged
- On failure / unknown form → status = `manual_required`, surfaced in dashboard

### Phase 7 — Scheduling + notifications (~0.5 days)

- APScheduler: 8:00am + 6:00pm daily (timezone: America/Mexico_City)
- Lockfile prevents overlapping runs
- macOS notification via osascript: "Applied 8 · Skipped 41 · 1 manual"
- Optional email digest configurable in config.toml

### Phase 8 — Dashboard (~1.5 days)

- FastAPI + HTMX (no JS build step)
- Views: All · By Status · By Role · By Score · Manual Required
- Per-row actions: mark interview/rejected, add note, view tailored CV, blacklist company
- Stats bar: total applied, response rate %, avg score, applications this week
- Source toggle: flip sources.enabled in DB — no code changes needed
- Score analytics: keyword vs SBERT vs LLM over time (MLOps layer)

---

## Timeline

| Days | Work                                                                 |
| ---- | -------------------------------------------------------------------- |
| 1–2  | Phase 0 ✅ + Phase 1 — environment ready, DB working, config defined |
| 3–5  | Phase 2 + 3 + 4 — jobs flowing in, scored, visible in dashboard      |
| 6–8  | Phase 5 + 6 — tailored CVs generating, first real applications       |
| 9–10 | Phase 7 + 8 — scheduler running, dashboard polished                  |

---

## Risks & Mitigations

| Risk                                  | Mitigation                                                                     |
| ------------------------------------- | ------------------------------------------------------------------------------ |
| 16GB tight when running 14B model     | Pipeline is sequential — never load two models simultaneously                  |
| qwen2.5:7b scores unreliably          | Start threshold at 80%+, validate first 30 scores manually, adjust             |
| LinkedIn blocks Playwright            | Prioritise Adzuna + Remotive + Decodo; Playwright is last resort               |
| Spammy tailored CVs                   | Review first 20 outputs manually before enabling full auto                     |
| Duplicate applications                | hash(company+title+location) is the source of truth, checked before every step |
| Mac sleeps mid-run                    | Enable "prevent sleep when plugged in" + schedule runs when at desk            |
| Prompt injection via job descriptions | Sanitize all job description text before passing to any LLM                    |
| LLM sycophancy (over-scoring)         | Compare LLM scores vs actual outcomes; recalibrate prompt if needed            |

---

## MLOps Skills Developed

| Skill                         | What you'll actually do                                           |
| ----------------------------- | ----------------------------------------------------------------- |
| Structured output reliability | JSON schema validation, retry logic, prompt constraints at scale  |
| Prompt versioning + evals     | Fixed test set of 20 jobs, score vs outcome correlation over time |
| Inference optimization        | Sequential model loading, tokens/sec measurement, 16GB management |
| Feedback loop design          | interview/rejected outcomes → scoring prompt recalibration        |
| Data pipeline design          | Multi-source ETL, dedup, normalisation, schema mismatch handling  |
| Model benchmarking            | BM25 vs SBERT vs LLM — which predicts real outcomes best?         |
| Observability + monitoring    | Score drift, source health checks, apply success rate over time   |
| Prompt injection defense      | Sanitizing untrusted job description input before LLM ingestion   |

### The MLOps maturity arc

```
Week 1–2:  "It works"            → pipeline runs, jobs scored, applied
Week 3–4:  "It works consistently" → output validation, retry, error handling
Month 2:   "I know if it's working" → eval harness, score vs outcome logging
Month 3:   "I can improve it"    → prompt versioning, A/B tests, benchmarks
Month 4+:  "I can explain every decision" → full observability, drift detection
```

---

_Last updated: Phase 0 complete. Ready to begin Phase 1._
