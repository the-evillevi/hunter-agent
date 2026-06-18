# hunter-agent

A small FastAPI, SQLite, SQLModel, and HTMX project for learning by building a
job tracker.

The long-term vision is a local-first job application assistant that will crawl
job sources, score listings against target profiles, prepare application
materials, and track outcomes in SQLite. The current repo is intentionally
smaller: it is growing from a tested FastAPI/SQLModel foundation toward that
vision one quest at a time.

> Automated job application assistant · MacBook Pro M5 16GB · Ollama · Python · SQLite

## Current State

The application currently uses:

| Layer           | Current tool                                              |
| --------------- | --------------------------------------------------------- |
| Language        | Python 3.14                                               |
| Package manager | uv                                                        |
| Web app         | FastAPI                                                   |
| Templates       | Jinja                                                     |
| Styling         | TailwindCSS via pnpm                                      |
| Database        | SQLite + SQLModel                                         |
| Config          | TOML + Pydantic validation                                |
| Tests           | pytest + FastAPI TestClient                               |
| Future helpers  | APScheduler and Playwright are installed for later quests |

The app code lives under `app/`, with `app/main.py` wiring the FastAPI
application, `app/routes/` owning HTTP endpoints, `app/services/` holding
business logic, `app/models/` defining SQLModel/Pydantic models, and `app/db/`
containing database helpers plus the local SQLite database.

## Quickstart

Install Python dependencies:

```sh
uv sync
```

Install frontend tooling:

```sh
pnpm install
```

Build Tailwind CSS:

```sh
pnpm css:build
```

Run the local app:

```sh
uv run uvicorn app.main:app --reload
```

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

## Project Layout

```text
hunter-agent/
├── app/
│   ├── main.py              # FastAPI app wiring
│   ├── config.py            # config.toml loading helpers
│   ├── db/                  # SQLModel session helpers and local SQLite DB
│   ├── models/              # SQLModel and Pydantic models
│   ├── routes/              # HTTP endpoints
│   ├── services/            # business logic
│   ├── static/              # Tailwind input/output CSS
│   └── templates/           # Jinja templates
├── sql/
│   ├── hunter-agent.sql     # schema reset script
│   └── seed.sql             # sample data
├── tests/                   # pytest suite
├── vault/                   # Obsidian project-management vault
├── config.toml              # app configuration
├── pyproject.toml           # Python project metadata and dependencies
└── package.json             # Tailwind and Prettier tooling
```

## Database Schema

`sql/hunter-agent.sql` is the source of truth for the local development schema.
It drops and recreates all tables, so do not run it against data you want to
keep.

```sql
CREATE TABLE companies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE locations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE keywords (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  role_name TEXT,
  salary_min INT,
  location_type TEXT CHECK (location_type IN ('remote', 'hybrid', 'onsite')),
  match_threshold INT CHECK (match_threshold BETWEEN 1 AND 100),
  active BOOLEAN NOT NULL CHECK (active IN (0, 1))
);

CREATE TABLE profile_keywords (
  profile_id INTEGER NOT NULL REFERENCES profiles (id),
  keyword_id INTEGER NOT NULL REFERENCES keywords (id),
  PRIMARY KEY (profile_id, keyword_id)
);

CREATE TABLE jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id INTEGER NOT NULL REFERENCES profiles (id),
  title TEXT NOT NULL,
  company_id INTEGER NOT NULL REFERENCES companies (id),
  location_id INTEGER NOT NULL REFERENCES locations (id),
  url TEXT UNIQUE,
  source_id INTEGER NOT NULL REFERENCES sources (id),
  description TEXT,
  hash TEXT UNIQUE,
  scraped_at DATETIME NOT NULL,
  score INT CHECK (score BETWEEN 1 AND 100),
  score_reasoning TEXT
);

CREATE TABLE applications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL UNIQUE REFERENCES jobs (id),
  cv_path TEXT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (
    status IN (
      'pending',
      'draft',
      'applied',
      'acknowledged',
      'interviews',
      'rejected',
      'ghosted',
      'offer',
      'accepted'
    )
  ),
  applied_at DATETIME,
  last_updated DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  notes TEXT
);

CREATE INDEX idx_applications_last_updated ON applications (last_updated);
CREATE INDEX idx_applications_status ON applications (status);

CREATE TABLE blacklist (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INTEGER REFERENCES companies (id),
  job_id INTEGER REFERENCES jobs (id),
  reason TEXT,
  added_at DATETIME NOT NULL,
  CHECK (
    (company_id IS NOT NULL AND job_id IS NULL)
    OR
    (company_id IS NULL AND job_id IS NOT NULL)
  )
);
```

## Configuration

`config.toml` currently contains:

- app metadata and paths, including `app/db/hunter-agent.db`
- scheduler settings
- Ollama scorer/tailor model settings
- target job profiles with keywords, salary floors, location preferences, and
  exclude keywords
- source settings for Adzuna, Remotive, and LinkedIn
- application form defaults

Avoid committing real credentials, API keys, or session cookies. Replace
placeholder values locally when a quest needs them.

## Target Vision

Hunter Agent will grow toward a local-first job application pipeline:

```text
APScheduler
  -> Job sources
  -> Deduplication and hard filters
  -> Keyword, semantic, and LLM scoring
  -> CV tailoring
  -> Draft or submitted applications
  -> SQLite audit log
  -> FastAPI + HTMX dashboard
```

The guiding rules are:

1. Never apply blindly; every application should be scored first.
2. Never duplicate; SQLite should deduplicate across runs.
3. Prefer legitimate APIs and data paths before browser automation or scraping.
4. Keep automation reviewable before it becomes autonomous.

## Planned Stack

These tools are part of the project vision and should be added through quests
when their implementation work begins:

| Layer              | Planned tool                  | Why                                           |
| ------------------ | ----------------------------- | --------------------------------------------- |
| Scheduling         | APScheduler                   | Runs inside the app, no cron needed           |
| Browser automation | Playwright                    | Handles JS-heavy application forms            |
| Local AI           | Ollama + Ollama Python client | Private local inference on Apple Silicon      |
| CV generation      | python-docx + PDF export path | Edit Word master and export application files |
| Semantic scoring   | sentence-transformers         | Local embedding-based similarity              |
| Keyword scoring    | rank-bm25                     | Deterministic keyword overlap                 |

### Model Strategy For 16GB Apple Silicon

The future AI pipeline should run sequentially so large models are not loaded at
the same time.

| Task         | Candidate model | Notes                                      |
| ------------ | --------------- | ------------------------------------------ |
| Scoring      | qwen2.5:7b      | Fast enough for larger batches of listings |
| CV tailoring | qwen2.5:14b     | Better writing for jobs above threshold    |

## Future Architecture Notes

The future crawler/scoring/application layers should follow protocol-style
interfaces: define a small contract, add implementations behind registries, and
keep orchestration code from importing concrete providers directly.

Expected future areas:

- job source adapters for Adzuna, Remotive, and later higher-risk sources
- deduplication by stable job identity, starting with `hash`
- deterministic keyword scoring before any LLM scoring
- optional semantic scoring once dependencies and test fixtures are in place
- prompt-injection defenses before job descriptions reach an LLM
- CV tailoring that never invents facts
- application automation that can stop at draft/review before full submission
- dashboard controls for status updates, notes, blacklisting, and score review

## Risks

| Risk                                  | Mitigation                                                 |
| ------------------------------------- | ---------------------------------------------------------- |
| Local models strain 16GB memory       | Keep model usage sequential and measure tokens/sec         |
| Scoring looks confident but is wrong  | Compare scores with real outcomes and keep manual review   |
| Browser automation gets blocked       | Start with public APIs and use Playwright only when needed |
| Duplicate applications                | Preserve unique job/application constraints in SQLite      |
| CV tailoring overstates experience    | Rewrite and reorder only facts already present in the CV   |
| Prompt injection via job descriptions | Sanitize untrusted listing text before LLM calls           |

## MLOps Learning Goals

This project should create practice with:

- structured output validation and retry logic
- prompt versioning and fixed evaluation sets
- local inference measurement
- feedback loops from interview/rejection outcomes
- multi-source data ingestion and deduplication
- model benchmarking across keyword, semantic, and LLM scores
- observability for score drift and source health

_Last updated: README aligned with the current repo structure and target quest roadmap._

## License

This project is source-available under the Hippocratic License 3.0 with the Copyleft, Workers on Board of Directors, Military Activities, and Mass Surveillance modules enabled.

## AI Assistance

This project was developed with substantial assistance from AI coding tools, including but not limited to:

- GPT5.5
- Sonnet 4.6
- DeepSeek V4 Flash Free
- Big Pickle

The maintainer is responsible for the project’s architecture, prompts, review,
testing, integration, documentation, and published form. AI-generated code has
been reviewed and modified as needed before inclusion.

Use of AI assistance does not grant any additional permissions beyond this
repository’s license.
