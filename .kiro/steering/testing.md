---
inclusion: always
---

# When to run tests and linting (read this first)

Do NOT run pytest or ruff after every individual edit, and do NOT re-run the
suite repeatedly during a task. Running them per-change wastes time.

The policy is:

- **While iterating / mid-task:** do not run the suite at all. At most, run the
  narrow set of tests directly covering the code you just changed
  (e.g. `pytest tests/<file>.py::<test>`) when you need to confirm a specific
  fix. Prefer reasoning over re-running.
- **Once, at the end of the task** (the single verification gate): run ruff,
  then the full suite. This is also the gate before any push, PR open/merge, or
  marking a Jira task Done. Run it one time, not after each sub-step.

If a task is split into spec tasks, treat the final verification as its own
step at the end of the whole feature, not a per-task action — unless a given
task explicitly asks for verification.

# Running tests

Run from the project root using the venv Python (see `environment.md`):

    & "d:\projects\bf_trader_py\.venv\Scripts\python.exe" -m pytest

Some integration tests are gated with `pytest.mark.skipif` (they skip unless
their required environment/credentials are present), so a plain run is safe to
use as the full suite.

# Linting and formatting (ruff)

Ruff runs at the SAME end-of-task gate as the test suite — NOT after each edit
or each file write. Run the lint and format checks once, as the first step of
the final verification gate, immediately before the full `pytest` run and
before any push, PR open/merge, or marking a Jira task Done. Ruff config lives
in `pyproject.toml` (line length 120).

Run ruff through the venv Python (`-m ruff`), NOT a bare `ruff` — ruff is
installed in `.venv` and is not on PATH, and the venv copy is the one that
matches CI (see `environment.md`).

    & "d:\projects\bf_trader_py\.venv\Scripts\python.exe" -m ruff check .           # lint
    & "d:\projects\bf_trader_py\.venv\Scripts\python.exe" -m ruff format --check .  # formatting (does not modify files)

Fix anything they flag before proceeding:

    & "d:\projects\bf_trader_py\.venv\Scripts\python.exe" -m ruff check --fix .     # auto-fix lint issues where possible
    & "d:\projects\bf_trader_py\.venv\Scripts\python.exe" -m ruff format .          # apply formatting

Recommended pre-push / pre-PR order:

    1. & "d:\projects\bf_trader_py\.venv\Scripts\python.exe" -m ruff check .  ;  & "d:\projects\bf_trader_py\.venv\Scripts\python.exe" -m ruff format --check .   # lint + format clean
    2. & "d:\projects\bf_trader_py\.venv\Scripts\python.exe" -m pytest                                          # full suite green
    3. push / open PR / mark Done
