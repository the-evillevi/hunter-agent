# Repository Guidelines

## Project Structure & Module Organization

`app/` contains the FastAPI application. `app/main.py` wires the app together, `app/routes/` owns HTTP endpoints, `app/services/` holds business logic, `app/db/` contains SQLModel session helpers and the local database, and `app/models/` contains SQLModel and Pydantic models. Jinja templates live in `app/templates/`; Tailwind input/output files live in `app/static/`.

`sql/` contains database scripts. `sql/hunter-agent.sql` is the schema and `sql/seed.sql` is sample data. `config.toml` is the main configuration file for paths, scheduler settings, job profiles, sources, and application defaults.

Tests live in `tests/` and should mirror the app structure, for example `tests/test_jobs.py`.

## Issue Tracking

The roadmap is tracked in Linear. Use your Linear skill to access the Hunter Team, Hunter Agent Project's issues, which correspond to this repository.

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

Use Python 3.14, `uvx ruff format`, SQLModel for database access, and Pydantic for validation at boundaries such as `config.toml`, forms, and scraper payloads. Keep modules small and beginner-readable. Prefer explicit function names such as `list_jobs`, `get_session`, and `scrape_jobs`.

Use `snake_case` for files, functions, variables, and route helpers. Use `PascalCase` for models such as `JobRecord` and `AppConfig`. Prefer Tailwind and DaisyUI utility classes in templates over hand-written CSS. Keep comments learning-focused: explain why a file or function exists, then leave `TODO` markers for unfinished implementation.

## Testing Guidelines

Use `pytest`. Name test files `test_*.py` and test functions `test_*`. Focus first on config validation, service/database behavior, and route rendering. FastAPI route tests use `fastapi.testclient.TestClient`.

Good early targets: Pydantic config validation, SQLModel session setup, `list_jobs`, and health route behavior.

## Commit Guidelines

Use Conventional Commits for commit messages. Agents should include their model name as the commit scope and always include a long description, for example `git commit -m "feat(gpt5.5): add job search filters" -m "[long description]"` or `git commit -m "fix(fable5): handle missing company names" -m "[long description]"`. The rules are enforced by Commitlint through Husky's `commit-msg` hook.

Keep commits atomic: each commit should be a small, self-contained unit that addresses one task or fix.

Run `pnpm install` after cloning or reinstalling dependencies so Husky can install the local Git hooks. To manually check a message, use:

```sh
pnpm exec commitlint --edit .git/COMMIT_EDITMSG
```

## Security & Configuration Tips

Treat `config.toml` as project configuration, but avoid storing real credentials in it. Keep API keys and session cookies out of commits. The local SQLite database is useful for development, but schema changes should be represented in `sql/` so other contributors can recreate the database.
