# Working on duebook

Read README.md first. These rules apply to every change, by people and by agents.

## Value is not measured in lines

- Change or extend what exists before adding something new. No parallel
  versions of a module, no per-round or per-experiment copies of a runner.
- A change that replaces code deletes the code it replaces, in the same pull
  request. No dead paths, no "legacy" branches kept "just in case".
- No speculative options, hooks or abstractions. Add a setting only when two
  real values are needed today.
- New dependencies only when they remove more than they add; experiment-only
  dependencies go in an optional extra.
- State in the pull request description how many lines it adds and removes,
  and why the growth is necessary if it grows.

## Code

- The core (`reducer`, `lifecycle`, `temporal`, `card`, `closing`) stays pure:
  no database, network or clock.
- Comments and docstrings say why, briefly. A docstring is a few lines, not an
  essay; the design history belongs in the pull request or docs/, not in code.
- Names over comments. Small functions over branching ones.

## Tests

- One test per rule, named after the rule. No near-duplicate tests that check
  the same path with different data unless the data is the point.
- `ruff check .` and the full suite pass at every commit, with
  `TEST_DATABASE_URL` pointing at a throwaway PostgreSQL database (the
  fixtures drop its public schema).

## Experiments

- The benchmark has one runner; a new round is a configuration, not a module.
- Commit summaries and the numbers the docs cite. Do not commit bulky raw
  records unless docs/results.md cannot be reproduced without them.

## Git

- Work on a branch, open a pull request into `main`, never push to `main`,
  never force-push.
- Every commit message ends with
  `Co-authored-by: Rustam Gasymov <88037667+Jericho2k@users.noreply.github.com>`.
