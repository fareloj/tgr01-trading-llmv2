# AGENTS.md

Instructions for AI agents working in this repository.

## Commit workflow (required)

Every commit in this repository follows a two-step review protocol. Do not skip
either step, and do not bundle a fix with its review into one motion.

### Before committing: one parallel validator

Launch **one** subagent (Task tool, `subagent_type: general`) to independently
check the work being committed. Give it the exact diff scope and ask for
`CONFIRMED-FIXED / STILL-BROKEN / NEW-ISSUE` verdicts with `file:line` evidence.
It should run the tests itself, not just read code.

### After committing: gpt-5.6-sol via Codex

Run a read-only adversarial pass with the Codex CLI (see the `codex-delegate`
skill). Always pass the effort explicitly with `-c 'model_reasoning_effort=...'`;
do not rely on `~/.codex/config.toml`, which the operator may set differently.
`medium` is the standard for this repository's reviews.

```powershell
codex exec "<scoped review prompt>" `
  -m gpt-5.6-sol -c 'model_reasoning_effort="medium"' `
  -s read-only `
  -o "C:\Users\danie\AppData\Local\Temp\opencode\review_out.txt" 2>&1
```

Then read the output file and act on every real finding. A finding is real only
after it is reproduced; verify with a counterexample or a failing test before
changing code.

If Codex reports a usage limit, note it and continue with the subagent validator
only; retry Codex when quota returns.

### Why both

The two reviewers fail differently. The subagent sees the repository with its own
context and runs tests; Codex/gpt-5.6-sol tends to find contract and security
edge cases. Four passes in one session produced real defects that neither found
alone, including a credential-boundary escape and a silently-coerced boolean.

## Non-negotiables in this repo

- Real trading stays disabled. `REAL_TRADING_ENABLED` must remain `false`. Never
  add an order, cancel, transfer or withdrawal endpoint.
- The deterministic Risk Manager is the only component allowed to approve an
  order. Never weaken it, and never let an LLM or the RAG approve, block or size
  an order.
- Never commit `.env` files or credentials. Never paste secrets into a prompt.
- Do not modify Risk Manager thresholds or the conviction gate without explicit
  operator sign-off. Prompt calibration must be fixed on the prompt side.
- Prefer failing closed. A block or a HOLD is a valid, safe outcome.

## Environment notes

- Python: use the project venv, `& ".\.venv\Scripts\python.exe"`. The global
  `py -3.11` does not have the project dependencies.
- PowerShell 5.1: no `&&`; chain with `;` or `cmd1; if ($?) { cmd2 }`.
- Tests require PostgreSQL: `docker compose up -d db`.
- Full suite: `& ".\.venv\Scripts\python.exe" -m pytest backend\tests -q`
- The historical dataset lives outside Git in `backend/data_exports/`.
