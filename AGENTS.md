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

## Architecture changes only by decision

The architecture below is deliberate. Work within it. Changing it is a
decision for the owner, made after serious consideration, never a side effect
of a task.

1. The event log is the source of truth. A card is the fold of the stored
   events and their stored proposals; it can always be rebuilt without a model.
2. The model proposes, the reducer decides. The core (`events`, `card`,
   `proposals`, `reducer`, `closing`, `slots`, `temporal`, `lifecycle`) is pure
   and imports nothing outside itself.
3. The model never computes a date; `temporal` resolves every deadline.
4. Statuses and alerts are functions of the clock (`lifecycle`); an alert is
   identified by `Notice.key` and delivered through the outbox.
5. Everything stored or shown carries its source message.
6. Dependencies point inward: core ← `extract` ← `store` ← `pipeline` ←
   `api` / `cli`. `bench` depends on the product, never the reverse.
7. Every model call goes through `extract/client.py` and its presets.
8. The database changes only through numbered migrations in `store/migrations`.

The closing rule and its historical replay behavior are recorded in
[decision 0001](docs/decisions/0001-verifier-veto.md); what happens to a
message the model cannot read, in
[decision 0002](docs/decisions/0002-unreadable-message.md); when a new
promise is a duplicate, in [decision 0003](docs/decisions/0003-promise-identity.md).

If a task cannot be done well within these rules, do not change them. Do the
part that fits, and write the proposed change as a decision record in
`docs/decisions/NNNN-title.md` (context, options considered, proposal,
consequences, status "proposed"), then say so in the pull request. A rule
changes only when the owner accepts that record; the record then states the
new rule, and this list is updated in the same pull request.

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
