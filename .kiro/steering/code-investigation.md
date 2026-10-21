---
inclusion: auto
---

# Verifying claims about the code (especially from tickets)

A real incident on SP-403 motivates this rule. A Jira ticket said a guard lived
in a function called `remove_stale_targets` and asked us to mirror it. A search
for that *name* returned nothing, and we wrongly concluded "the guard does not
exist." It did exist — in a differently-named function (`process_targets`) — and
a content search for the behaviour (`if not result`, `empty result`, `EXPIRED`)
finds it instantly. The error was a verification/search-strategy failure, NOT a
tooling failure: `grep_search` was retested and works reliably in this workspace.

## Rules

- **Never assert "X does not exist in the code" from a name search alone.** A
  ticket, comment, or memory may have the wrong name for a function/variable.
  Before claiming something is absent, search for the *behaviour or distinctive
  strings*, not just the identifier you were handed:
  - search the literal log/message/SQL strings (e.g. `returned empty result`,
    `marking as EXPIRED`),
  - search the pattern (`if not result`, `\.get\("result"\)`),
  - read the whole candidate file(s) rather than trusting a single grep miss.
- **Treat ticket-supplied names as hints, not facts.** Confirm the actual
  function/variable name in the source before repeating it in a spec, and
  correct the ticket's wording when it is wrong (say so explicitly).
- **A zero-hit search is evidence about the query, not proof of absence.** If a
  search returns nothing for something you expect to exist, vary the query
  (synonyms, the behaviour, a unique nearby string) before concluding it is
  missing.

## grep_search usage notes (this workspace)

`grep_search` is reliable here, but a few habits avoid false "not found"s:

- It is **regex (Rust)**, so escape regex metacharacters in literal searches:
  `(`, `)`, `[`, `]`, `{`, `}`, `+`, `*`, `?`, `^`, `$`, `|`, `.`, `\` must be
  escaped with `\\`. Searching `result[0]` literally will not match — use
  `result\[0\]`.
- Results are **capped at 100 matches** and long lines are **truncated**
  (`[truncated: line too long]`); a truncated line can hide the token you want.
  Narrow with `includePattern` (e.g. `**/monitor_service.py`) when you know the
  file, and read the file directly to confirm an absence.
- Scope is the **open workspace folder(s) only**. Paths outside them (including
  `../` escapes or absolute paths elsewhere) return no matches rather than an
  error — do not read that as "absent from the codebase."
- When in doubt, cross-check with `read_file` on the specific function; a direct
  read is the authority, a single grep miss is not.
