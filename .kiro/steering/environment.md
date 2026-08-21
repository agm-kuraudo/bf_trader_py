---
inclusion: auto
---

# Project Environment

## Python

- Virtual environment location: `.venv`
- Python executable: `d:\projects\bf_trader_py\.venv\Scripts\python.exe`
- To run Python: `& "d:\projects\bf_trader_py\.venv\Scripts\python.exe"`
- To run pytest: `& "d:\projects\bf_trader_py\.venv\Scripts\python.exe" -m pytest`

## Shell

- The command shell is **PowerShell**.
- Do NOT use `&&` to chain commands - it is invalid in PowerShell. Use `;` or make separate calls.
- Use `& "path\to\exe"` syntax to invoke executables with special paths.
- Set working directory via the `cwd` parameter, not `cd`.
