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
