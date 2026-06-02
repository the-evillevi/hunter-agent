# hunter-agent

A small FastAPI, SQLite, SQLModel, and HTMX project for learning by building a
job tracker.

## Application Tracker Learning Roadmap

Work through these tasks in ID order. The application exercises in
`tests/test_applications.py` are skipped initially so the existing test suite
stays green. Remove a test's `@pytest.mark.skip` decorator when you start its
related task.

| ID     | Task                                                                                                     | Status |
| ------ | -------------------------------------------------------------------------------------------------------- | ------ |
| APP-01 | Make SQL `applications.applied_at` nullable to match pending application behavior.                       | DONE   |
| APP-02 | Remove the duplicate applications foreign-key declaration.                                               | DONE   |
| APP-03 | Recreate `app/db/hunter-agent.db` from the schema and seed scripts after the schema fixes.               | DONE   |
| APP-04 | Align required SQLModel job and profile fields with SQL `NOT NULL` constraints.                          | DONE   |
| APP-05 | Add `ApplicationListItem` and implement `list_applications()` with joins to jobs and companies.          | TODO   |
| APP-06 | Register the applications router in `app/main.py`.                                                       | TODO   |
| APP-07 | Render title, company, status, and timestamps in the applications partial, including an empty state.     | TODO   |
| APP-08 | Remove obsolete application placeholders after the SQLModel service works.                               | DONE   |
| APP-09 | Add temporary SQLite fixtures and FastAPI dependency overrides for isolated tests.                       | TODO   |
| APP-10 | Activate the schema-and-seed replay test.                                                                | DONE   |
| APP-11 | Add an HTMX status-update endpoint that refreshes `last_updated` and sets `applied_at` when appropriate. | TODO   |

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
