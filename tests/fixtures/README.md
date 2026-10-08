# Test fixtures

`lf52_prefix.jsonl.gz` is derived from the published ARC-AGI-3 journal of the
lf52 run (`evidence/arcagi/journal-lf52.jsonl.gz`, MIT): events 0
to 82, reduced to the fields a behavior module reads (`id`, `action`, `data`,
`counts_action`, `state`, `levels_completed`, `level_before`, `win_levels`,
`available_actions`, `predict`, `predict_ok`, the grade records cut to `kind`,
`ok` and `text`), with only the settled frame of each event kept. Event 82
re-issues the move that graded FALSE at event 81, which is the halt the
coverage audit must raise. The reduction keeps the fixture small and is not a
journal: it has no chain and is not meant to verify.

`cli_help.json` is the help text of `assay` and of every one of its
commands and sub-commands, rendered by the hand-written parser at b1fb581
with `COLUMNS=80`, keyed by the command path (`""` for the root, `"state
declare"` for a sub-command). `tests/test_cli_help.py` holds the parser
built from the operation table to it, byte for byte.
