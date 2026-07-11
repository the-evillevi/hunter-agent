# hunter-agent

A FastAPI, SQLite, SQLModel, and HTMX project for learning by building a
job tracker.

The long-term vision is a local-first job application assistant that will crawl
job sources, score listings against target profiles, prepare application
materials, and track outcomes in SQLite.

> Automated job application assistant · MacBook Pro M5 16GB · Codex/Ollama · Python · SQLite

## Current State

The application currently uses:

| Layer           | Current tool                                              |
| --------------- | --------------------------------------------------------- |
| Language        | Python 3.14                                               |
| Package manager | uv                                                        |
| Web app         | FastAPI                                                   |
| Templates       | Jinja                                                     |
| Styling         | TailwindCSS and Daisy UI via pnpm                         |
| Database        | SQLite + SQLModel                                         |
| Config          | TOML + Pydantic validation                                |
| Tests           | pytest + FastAPI TestClient                               |
| Future helpers  | APScheduler and Playwright are installed for later quests |

The app code lives under `app/`, with `app/main.py` wiring the FastAPI
application, `app/routes/` owning HTTP endpoints, `app/services/` holding
business logic, `app/models/` defining SQLModel/Pydantic models, and `app/db/`
containing database helpers plus the local SQLite database.

### HTML and HTMX route conventions

Full HTML pages use the resource path, such as `/companies` and `/jobs`.
Dedicated fragment reads use `/{resource}/partials/{fragment}`, such as
`/companies/partials/table` or `/jobs/partials/list`. Mutations keep meaningful
resource or action URLs even when their response is an HTML fragment; returning
HTML alone does not put a route under `/partials`.

### API documentation

FastAPI serves interactive Swagger docs at `/docs` and the raw schema at
`/openapi.json`. Their audience is API consumers and automation: only JSON
operations appear there, grouped under intentional tags (`Monitoring`,
`Ingestion`) with summaries and typed success/error responses. Every HTML and
HTMX route — full pages, fragment reads, and fragment-returning mutations — is
an internal UI surface and sets `include_in_schema=False`, so the browser UI
never leaks into the API docs. `tests/test_openapi.py` enforces this policy;
if you add a route, that test tells you whether it must be tagged and
summarized (JSON API) or excluded (UI).

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

## Issue Tracking

The project roadmap and issue tracking lives in Linear

- Team: Hunter
- Project: Hunter Agent

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

S&P 500 metadata is stored directly on `companies` for this iteration. `ticker`
is globally unique and acts as the single symbol identity across providers;
`cik` stays nullable because the SSGA holdings workbook `Identifier` value is
not guaranteed to be a CIK. The first source of truth is the SSGA State Street
SPDR S&P 500 ETF Trust holdings workbook, followed by Wikipedia enrichment and
then likely Slickcharts. The `sp500_tier` values are constrained to `mag7`,
`top100`, `top200`, `top300`, `top400`, and `top500`; `mag7` means Apple,
Microsoft, Nvidia, Amazon, Alphabet, Meta, and Tesla, while `top100` excludes
those Magnificent 7 companies and the remaining buckets follow rank ranges.
`sp500_rank_source` and `sp500_rank_status` distinguish true SSGA/SPY
weight-derived rank from explicitly marked fallback/source-order enrichment.
Companies removed from the index are represented in `removed_sp500_companies`
with a removal date instead of being folded into the active company row.

## Configuration

`config.toml` currently contains:

- app metadata and paths, including `app/db/hunter-agent.db`
- scheduler settings
- Ollama scorer/tailor model settings
- source settings for Adzuna, Remotive, LinkedIn, and the SSGA/SPY holdings
  workbook
- application form defaults

Avoid committing real credentials, API keys, or session cookies. Replace
placeholder values locally when a quest needs them.

## Job Profiles

Job profiles are stored in SQLite and managed from `/profiles`. Each profile
owns its role, active state, salary floor, match threshold, location types,
included keywords, excluded keywords, and versioned source queries. Provider
credentials and provider-wide limits remain in `config.toml`.

Remotive profile queries support a constrained category, an optional company
selected from the companies table, free-form search text, and a result limit
from 1 through 10. Future adapters can register their own validated query JSON
schema without adding provider-specific columns to the core profile table.

## Manual S&P 500 Company Ingestion

Hunter Agent can manually import S&P 500 constituents from the SSGA State
Street SPDR S&P 500 ETF Trust holdings workbook. Scheduling is not wired yet;
the reusable ingestion service is the boundary a future APScheduler job should
call.

Start the app, open the dashboard, and select **Run ingestion** in the **S&P 500
company ingestion** card. For scripts or terminal use, call the same workflow
through the JSON endpoint:

```sh
curl --fail-with-body --request POST \
  http://127.0.0.1:8000/api/companies/sp500/ingest
```

A successful response has this shape (the numbers below are illustrative):

```json
{
  "status": "success",
  "sources": ["ssga_spy_holdings"],
  "created": 490,
  "updated": 8,
  "unchanged": 5,
  "removed_from_index": 1,
  "failed": 0,
  "failures": []
}
```

The endpoint returns `200` for complete success, `409` when no company source
is enabled, `502` for provider/download/normalization failures, and `500` for
persistence failures. Enabled providers continue independently, so a response
can have `status: "partial_failure"` and contain both imported counts and
failure details. Any failed authoritative SSGA fetch or normalization is
discarded before persistence so an incomplete workbook cannot incorrectly mark
existing companies as removed.

### Source enablement and workbook location

The stable source name is `ssga_spy_holdings`. When a matching row exists in
the SQLite `sources` table, its `enabled` value is authoritative. If the row is
absent, `sources.ssga_spy_holdings.enabled` in `config.toml` is the fallback.
Unrelated job-source rows do not affect company ingestion.

By default the source downloads the official workbook:

```text
https://www.ssga.com/us/en/individual/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx
```

For offline development, set a project-relative or absolute local override:

```toml
[sources.ssga_spy_holdings]
enabled = true
workbook_url = "https://www.ssga.com/us/en/individual/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx"
workbook_path = "./tests/fixtures/ssga_spy_holdings_representative.xlsx"
```

Omit `workbook_path` to resume downloading from `workbook_url`. Do not commit a
machine-specific Downloads path.

### Workbook and ranking semantics

The workbook must contain a `holdings` sheet. Metadata rows near the top include
the fund name, ticker symbol, and holdings-as-of date. The holdings header must
provide `Name`, `Ticker`, and `Weight`; supported optional columns are
`Identifier`, `SEDOL`, `Sector`, `Shares Held`, and `Local Currency`.

`Weight` is the authoritative input for descending `sp500_weight_rank`.
`sp500_tier` stores `mag7` for Magnificent Seven constituents and otherwise
uses the `top100`, `top200`, `top300`, `top400`, or `top500` rank bucket.
The workbook can contain more than 500 securities because some index companies
have multiple share classes; a valid rank above 500 has no top-N tier.
`sp500_rank_source` and `sp500_rank_status` identify weight-derived rankings so
future enrichment cannot silently masquerade as authoritative rank data.

The workbook's `Identifier` is stored in `sp500_identifier` and is not assumed to
be a CIK. Wikipedia is a later enrichment source, while Slickcharts is a possible
third provider; neither replaces SSGA/SPY as the first source of truth.

### Verify an import

Run the focused tests and inspect the active and removed constituent counts:

```sh
uv run pytest tests/test_sp500_ingestion.py tests/test_sp500_company_import.py -q
sqlite3 app/db/hunter-agent.db \
  "SELECT COUNT(*) FROM companies WHERE is_sp500 = 1;"
sqlite3 app/db/hunter-agent.db \
  "SELECT COUNT(*) FROM removed_sp500_companies;"
```

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
| AI Provider        | Codex                         | GPT5.5 for heavy lifting.                     |
| CV generation      | python-docx + PDF export path | Edit Word master and export application files |
| Semantic scoring   | sentence-transformers         | Local embedding-based similarity              |
| Keyword scoring    | rank-bm25                     | Deterministic keyword overlap                 |

### Model Strategy For 16GB Apple Silicon

The future AI pipeline should run sequentially so large models are not loaded at
the same time.

| Task         | Candidate model | Notes                                      |
| ------------ | --------------- | ------------------------------------------ |
| Scoring      | qwen2.5:14b     | Fast enough for larger batches of listings |
| CV tailoring | GPT5.5          | Better writing for jobs above threshold    |

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
