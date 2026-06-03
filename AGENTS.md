# Repository Guidelines

## Project Structure & Module Organization

`app/` contains the FastAPI application. `app/main.py` wires the app together, `app/routes/` owns HTTP endpoints, `app/services/` holds business logic, `app/db/` contains SQLModel session helpers and the local database, and `app/models/` contains SQLModel and Pydantic models. Jinja templates live in `app/templates/`; Tailwind input/output files live in `app/static/`.

`sql/` contains database scripts. `sql/hunter-agent.sql` is the schema and `sql/seed.sql` is sample data. `config.toml` is the main configuration file for paths, scheduler settings, job profiles, sources, and application defaults.

Tests live in `tests/` and should mirror the app structure, for example `tests/test_jobs.py`.

## Quest Tracking

The roadmap is tracked in the Obsidian vault. `vault/HNTR Quests.base` is the canonical quest list, and each quest note lives in `vault/quests/` with an `HNTR-*` ID.

When working on roadmap items, preserve the `HNTR-*` IDs and update the matching quest note properties when status or scope changes. Do not reintroduce task-tracking tables in `README.md`; README is for human orientation and should point to the vault/base instead.

Quest notes use the Proximity Scale in their `points` property: `Cantrip`, `Uncharted Territory`, or `Here Be Dragons`.

## Build, Test, and Development Commands

Use `uv` for dependency management:

```sh
uv sync
```

Use `pnpm` for Tailwind tooling:

```sh
pnpm install
pnpm css:build
pnpm css:watch
```

Run the local app:

```sh
uv run uvicorn app.main:app --reload
```

Compile-check Python files:

```sh
uv run python -m compileall app
```

Inspect the SQLite database:

```sh
sqlite3 app/db/hunter-agent.db ".tables"
sqlite3 app/db/hunter-agent.db ".schema"
```

Run tests:

```sh
uv run pytest
```

Always run Ruff and Prettier auto-formatters after finishing a change:

```sh
uvx ruff format
pnpm format
```

Check Ruff and Prettier formatting without changing files:

```sh
uvx ruff format --check
pnpm format:check
```

## Coding Style & Naming Conventions

Use Python 3.14, four-space indentation, SQLModel for database access, and Pydantic for validation at boundaries such as `config.toml`, forms, and scraper payloads. Keep modules small and beginner-readable. Prefer explicit function names such as `list_jobs`, `get_session`, and `scrape_jobs`.

Use `snake_case` for files, functions, variables, and route helpers. Use `PascalCase` for models such as `JobRecord` and `AppConfig`. Prefer Tailwind utility classes in templates over hand-written CSS. Keep comments learning-focused: explain why a file or function exists, then leave `TODO` markers for unfinished implementation.

## Testing Guidelines

Use `pytest`. Name test files `test_*.py` and test functions `test_*`. Focus first on config validation, service/database behavior, and route rendering. FastAPI route tests use `fastapi.testclient.TestClient`.

Good early targets: Pydantic config validation, SQLModel session setup, `list_jobs`, and health route behavior.

## Security & Configuration Tips

Treat `config.toml` as project configuration, but avoid storing real credentials in it. Keep API keys and session cookies out of commits. The local SQLite database is useful for development, but schema changes should be represented in `sql/` so other contributors can recreate the database.
