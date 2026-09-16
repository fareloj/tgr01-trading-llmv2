---
name: codex-delegate
description: Use when a task needs a second, more capable opinion or a parallel independent reviewer - "delegue para o gpt5.6 sol", "codex", "codex exec", "redteam", "revisao adversarial", "abrir a sessao antiga", "resume session", "segunda opiniao". Drives the OpenAI Codex CLI (gpt-5.6-sol) from this repository for read-only audits, red-team checks and resuming an existing Codex session.
---

# Delegating to Codex CLI (gpt-5.6-sol)

Codex is a separate agent with its own login and its own sandbox. It is useful
as an **independent reviewer**: it does not share this session's context, so it
can catch assumptions that survived here. Treat its output as evidence to
review, never as authority.

## Environment facts (validated)

- CLI: `codex` is on PATH (`C:\Users\danie\AppData\Roaming\npm\codex.cmd`), version `0.154.0`.
- Default model and effort come from `~/.codex/config.toml`:
  `model = "gpt-5.6-sol"`, `model_reasoning_effort = "medium"`.
- This repo `D:\tgr01-trading-llmv2` is registered as `trusted`.
- Sessions live at `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`.
- A session opened by the Codex Desktop app holds an OS-level lock at
  `~/.codex/thread-writer-locks/<session_id>.lock`. Resuming it fails with
  `thread-store conflict: ... already has an active writer` until the desktop app releases it.

## Flag rules that are easy to get wrong

- `codex exec` accepts `-s/--sandbox <mode>` (`read-only`, `workspace-write`, `danger-full-access`).
- `codex exec resume` does **NOT** accept `--sandbox`. Pass it as config instead:
  `-c 'sandbox_mode="read-only"'`.
- Reasoning effort override: `-c 'model_reasoning_effort="medium"'`.
- Model override: `-m gpt-5.6-sol` (or any model the account can use).
- Read-only sandbox is the default for `codex exec`, but be explicit anyway.
- Final message only: `-o <file>` writes the last agent message to a file.
- Machine-readable stream: `--json` (JSONL on stdout; progress goes to stderr).
- Outside a git repo, add `--skip-git-repo-check`.

## Default invocation: read-only audit

Prefer this shape for audits and red-team checks. It never edits files.

```powershell
codex exec "<tarefa>" `
  -m gpt-5.6-sol -c 'model_reasoning_effort="medium"' `
  -s read-only --skip-git-repo-check `
  -o "C:\Users\danie\AppData\Local\Temp\opencode\codex_out.txt" 2>&1
```

Then read the output file. Keep prompts scoped: state the repo, the branch, the
exact files, the deliverable, and explicitly forbid edits when you want a review.

## Resuming an existing session

```powershell
codex exec resume <SESSION_ID> "<nova mensagem>" `
  -m gpt-5.6-sol -c 'model_reasoning_effort="medium"' `
  -c 'sandbox_mode="read-only"' `
  -o "C:\Users\danie\AppData\Local\Temp\opencode\codex_resume.txt" 2>&1
```

- `--last` picks the most recent session instead of an explicit id.
- If you hit `already has an active writer`, the Codex Desktop app has that
  session open. Options: close it there, fork a new session instead, or resume a
  different session id. Do not delete the lock file while the app is running.

## Reading a session without the CLI

Session files are JSONL. The useful shapes:

- line type `session_meta` -> `payload.cwd`, `.git.branch`, `.git.commit_hash`, `.session_id`.
- `response_item` with `payload.type == "message"` -> `payload.role` (`user`/`assistant`) and `payload.content[*].text`.
- `response_item` with `payload.type == "function_call"` -> `payload.name`.
- `event_msg` with `payload.type == "task_complete"` -> `payload.last_agent_message` (the final answer).

Use Python (the repo venv) with `PYTHONIOENCODING=utf-8`; PowerShell 5.1 will
crash on non-cp1252 characters such as `≠` in session text.

## Guardrails for this repository

- Real trading stays off. Never ask Codex to enable `REAL_TRADING_ENABLED`,
  place orders, or alter the Risk Manager's authority.
- Never paste secrets, API keys, OAuth tokens, or `.env` contents into a prompt.
  Codex runs under the operator's account and its transcripts are persisted.
- Prefer `-s read-only`. Use `workspace-write` only for a task the operator
  explicitly authorized, and review the diff afterwards with `git diff`.
- Codex is a reviewer, not an approver. Findings must be re-checked against the
  code before they change anything.
- Run red-team passes on a schedule, not once: the project expects recurring
  adversarial review with a stronger model.
