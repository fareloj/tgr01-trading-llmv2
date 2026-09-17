---
name: agent-delegate
description: Use when delegating work to another model or agent - "delegar", "delegate", "subagente", "agente paralelo", "worktree", "isolamento", "retomar sessao", "resume", "sessao", "qual modelo usar", "quota", "Ollama Cloud", "Claude Code", "OpenCode Go", "model allowlist", "parallel agents". Covers provider/model selection, session creation and resume for OpenCode and Claude Code, git worktree isolation, the quota policy, and the mandatory pre-commit double red-team. Read this before spawning any agent that can change files.
---

# Delegating to agents

This repository is worked by a single orchestrator plus, when justified, a small
number of parallel agents. Five agents is a **ceiling, not a target**. Zero is a
valid number. Adding an agent must buy real parallelism, independent review, or
genuine cognitive diversity - not ceremony.

Everything below was validated in this environment on 2026-09-16. Do not invent
flags, model ids or endpoints. Re-verify with `opencode models`, `claude --help`
and the official docs before trusting a written command.

## Non-negotiable rules

These mirror `AGENTS.md` in intent, plus a few additional delegation safeguards.
`AGENTS.md` is the authority; this section is a checklist, not a replacement.

1. **Real trading stays off.** `REAL_TRADING_ENABLED` must remain `false`. Never
   **add** an order, cancel, transfer or withdrawal endpoint, and never place a
   live order. Any authenticated trading call stays dry-run (`MB_DRY_RUN_*`);
   read-only authenticated requests are unaffected.
2. **The deterministic Risk Manager is the only component allowed to approve an
   order.** Never weaken it, and never let an LLM, an agent or the RAG approve,
   block or size an order. Do not change Risk Manager thresholds or the
   conviction gate without explicit operator sign-off; fix calibration on the
   prompt side.
3. **Never commit `.env` files or credentials.** Never paste secrets, API keys,
   OAuth tokens or `.env` contents into a prompt. Use placeholders when
   structural context is enough; agent transcripts are persisted by third
   parties.
4. **Prefer failing closed.** A block or a HOLD is a valid, safe outcome.
5. **One writer per file.** Two **write-capable** agents must never hold the same
   working tree. Read-only agents may share one - see the isolation section.
6. **No direct merge to `main`.** The orchestrator integrates.
7. **No force-push** on any branch.

## Model policy (validated allowlist)

The two OpenCode Go subscriptions must last the month. **Ollama Cloud is the
default provider for heavy work.** Go is reserved for what only Go has, or for
the single genuinely hard call.

**Sanctioned by role:**

| Role | Model | Provider |
|---|---|---|
| Workhorse implementation | `glm-5.3-flash` | `ollama-cloud` |
| Coding / agentic | `kimi-k2.7-code` | `ollama-cloud` |
| Deep specialist (rare) | `glm-5.3` | `ollama-cloud` |
| Reasoning only (not routine) | `gpt-5.6-luna` `--variant high` | `opencode-go` |
| Red-team A (mandatory) | `glm-5.3-flash` | `ollama-cloud` |
| Red-team B (mandatory) | `union-alpha` | `opencode` (free, unlimited) |
| Research / reading | orchestrator + `explore` subagent | - |

**Vetoed outright - never delegate to these:**

- `kimi-k3` (any provider). Almost no intelligence gain for the price; a single
  task can consume ~30% of the 5-hour window.
- `deepseek-v4-pro` (any provider). Inferior to `deepseek-v4.1-flash`.
- `muse-spark-1.3-contributor` and `muse-spark-1.2-contributor`. Prompts and
  completions are used to train Meta models (not ZDR). Never use for
  red-team, security, credentials, risk logic or order paths.

`glm-5.3` is allowed but expensive: it supports roughly 100-300 calls
(~10-15% of the 5-hour window). Do not make it the default.

`union-alpha` is free and unlimited on both `opencode/union-alpha` and
`opencode-go/union-alpha`. Use `opencode/union-alpha` for the pre-commit review so
Go quota stays untouched.

**Never read `~/.codex/config.toml` for the effort setting** - the operator may
change it. Always pass effort explicitly (see codex-delegate skill).

## Reasoning / effort control

- **OpenCode:** `--variant <value>`. The accepted values are **provider-specific**
  - there is no universal enum. Check the real list per model with
  `opencode models --verbose` before passing one. `union-alpha` and
  `kimi-k2.7-code` expose **no** variants - do not pass one.
- **Claude Code:** `--effort <low|medium|high|xhigh|max>`.
- **Raw Ollama:** `think: true|false|"low"|"medium"|"high"|"max"` in `/api/chat`.

Effort is **not** a no-op. Measured on `opencode-go/gpt-5.6-luna` (reasoning
tokens on the same prompt): `none` -> 0, `low` -> 86, `high` -> 121,
`xhigh` -> 112, `max` -> 210. That model's variants are
`{none, low, medium, high, xhigh, max}`.

An **invalid** variant value is not always rejected: `--variant bogus` on
`gpt-5.6-luna` was accepted silently and behaved like no override. Never assume
a variant took effect because the command did not error - confirm it changed the
result.

Do **not** apply an effort override blindly to a new model. An unsupported value
can make the model spend the whole budget on hidden reasoning and return an
**empty body**. Measure the contract first, as recorded in
`backend/agents/model_config.py`.

## Session continuity (validated end to end)

### OpenCode - create, capture id, resume

```powershell
# 1. new session; the id is in the JSON as "sessionID":"ses_..."
$out = opencode run "<prompt>" -m ollama-cloud/glm-5.3-flash --variant low --format json 2>&1
$sid = [regex]::Match(($out -join "`n"), 'ses_[A-Za-z0-9]+').Value

# 2. resume exactly that session, context preserved
opencode run "<follow-up>" -s $sid -m ollama-cloud/glm-5.3-flash --variant low --format json
```

Useful flags: `--dir <path>` to point the agent at a worktree, `-f <file>` to
attach a file, `--agent <name>` to pick a primary agent, `-c/--continue` for the
last session in the directory, `--fork` to branch a session (requires `-c` or
`-s`).

```powershell
opencode session list -n 20 --format json   # ids, titles, cwd
opencode session delete <sessionID>
```

**Verified pitfall - always confirm the model that actually ran.** With a long
multi-line prompt passed as a command-line argument, `-m <provider>/<model>` can
be **silently dropped** and OpenCode falls back to the default model. A short
prompt honoured `-m`; a ~4 KB multi-line prompt did not, and produced a review
from the wrong model. Defenses:

1. Put the long instructions in a file and pass it with `-f`, keeping the
   command-line message short.
2. **Verify the model from the session record, not from stdout.** `--format json`
   emits `sessionID`, events, tokens and cost but **no** model or provider, and
   the human-readable banner only appears in the default (non-JSON) format. So the
   reliable check is to read the session back:

   ```powershell
   $sid = [regex]::Match(($out -join "`n"), 'ses_[A-Za-z0-9]+').Value
   $exp = opencode export $sid 2>&1 | Out-String
   [regex]::Matches($exp, '"modelID"\s*:\s*"([^"]*)"') | ForEach-Object { $_.Groups[1].Value }
   [regex]::Matches($exp, '"providerID"\s*:\s*"([^"]*)"') | ForEach-Object { $_.Groups[1].Value }
   ```

   Confirm the pair is the model you asked for before trusting the review. In the
   default format the banner line `> build` + middle dot + model id is also
   visible, and that is the only place it appears.

PowerShell 5.1 also writes a UTF-8 BOM with `Out-File`/`Set-Content -Encoding
utf8`, and a BOM makes the attached file read as **binary** and fail the
attachment. Write files with `[System.IO.File]::WriteAllText($path, $text,
(New-Object System.Text.UTF8Encoding($false)))` to omit the BOM.

### Claude Code - create, capture id, resume

Verified: `--resume` **works** with Ollama models and does **not** hang.

```powershell
$env:ANTHROPIC_BASE_URL = "http://localhost:11434"
$env:ANTHROPIC_AUTH_TOKEN = "ollama"     # required but ignored by the daemon
$env:ANTHROPIC_API_KEY = ""              # must be empty
$sid = [guid]::NewGuid().ToString()

claude --model glm-5.3-flash:cloud --session-id $sid `
  -p "<prompt>" --output-format json

claude --model glm-5.3-flash:cloud --resume $sid `
  -p "<follow-up>" --output-format json
```

Other flags that matter here: `-c/--continue`, `--fork-session`,
`--effort <level>`, `--allowedTools "Read,Bash(git diff)"`, `--disallowedTools`,
`--permission-mode`, `--add-dir <dir>`, `-w/--worktree [name]`, `--bg`.
`ollama launch claude --model <model>` configures the connection for you. The
Ollama CLI is installed in this environment at
`C:\Users\danie\AppData\Local\Programs\Ollama\ollama.exe` (version 0.34.1) and is
on `PATH`. If a sandbox cannot see it, that is a sandbox limitation - verify with
`ollama --version` before assuming it is missing.

Two caveats, both verified:

- A harmless warning `[claude-code:unrecognized_model] {"query_source":
  "generate_session_title"}` appears because Claude Code tries to title the
  session with a Claude model name. It does not affect the answer or the resume.
- `total_cost_usd` in the JSON is **fictional** - Claude Code prices every call
  as if it were an Anthropic model. The real spend is Ollama quota. Never use
  that field to estimate cost.

The operator's `~/.claude/settings.json` sets `defaultMode: dontAsk`. Per the
official permission docs, `dontAsk` **auto-denies** every call that would
otherwise prompt; actions that need no approval (such as reads inside the
working directory) and tools pre-approved via `permissions.allow` still run.
It does **not** auto-approve edits or arbitrary shell commands. Pass explicit
`--allowedTools` / `--permission-mode` / `permissions.allow` rules for any agent
with file ownership, or its edits and commands will be denied and the agent will
look stuck.

### Ollama has no sessions

The Ollama API is **stateless**. There is no persistent agent or session to
resume; "resuming" means resending the message history. Real session continuity
comes from the CLI layer. This is why Ollama models are driven **through
OpenCode or Claude Code** rather than called raw.

## Git isolation for any agent that can write

Read-only agents (research, review, red-team) do not need a branch. Any agent
that can change a file must get its own branch **and** worktree.

Keep worktrees **outside** the repository so they never pollute `git status`:

```powershell
# branch: agent/<slot>/<task>   worktree: D:\tgr01-worktrees\<slot>
git worktree add -b agent/opencode-1/fix-rsi "D:\tgr01-worktrees\opencode-1" HEAD
opencode run "<task>" -m ollama-cloud/kimi-k2.7-code --dir "D:\tgr01-worktrees\opencode-1"

# teardown
git worktree remove "D:\tgr01-worktrees\opencode-1"
git worktree prune
git branch --delete agent/opencode-1/fix-rsi
```

`git branch -D` is denied by `opencode.json`. `git worktree remove --force` is
**not** denied by this repository's config - the deny list does not cover it, so
it is an unguarded destructive command. Do not run it. Use `--delete` for the
branch (which refuses an unmerged branch) and let the merge happen first.

Record which files each owner holds **before** launching, so two agents cannot
claim the same path.

## Adaptive delegation ladder

Pick the smallest rung that works.

| Situation | Agents |
|---|---|
| Trivial, or you can do it yourself | 0 |
| Small / medium bug | 0, or 1 |
| Complex bug | 1 investigates, 1 implements or reviews |
| Important change | 1 implementer + 1 independent reviewer |
| Critical change | 2-3 investigate **different hypotheses**; exactly one owns the implementation of a given area; the rest review, design tests or red-team |

Use 4-5 only when the parallelism is real.

### Parallel slots (maximum)

```
D:\tgr01-worktrees\               # branch mapping only, NOT inside the repo
  opencode-1/  -> agent/opencode-1/<task>
  opencode-2/  -> agent/opencode-2/<task>
  opencode-3/  -> agent/opencode-3/<task>
  ollama-1/    -> agent/ollama-1/<task>
  ollama-2/    -> agent/ollama-2/<task>
```

The orchestrator works in the main checkout and only integrates; it does not get
a worktree of its own.

Create only the ones you actually need.

## Quota policy

- Default to `ollama-cloud`. Routine work must not touch Go quota.
- `union-alpha` is currently zero-priced (`opencode models` reports 0 cost) and
  Go lists it as unlimited for a limited time. It is the pre-commit reviewer B,
  run on `opencode/union-alpha` (Zen, not Go) so Go quota stays untouched.
- The pre-commit reviewer A GLM 5.3 Flash runs on `ollama-cloud`, so the
  mandatory double review costs **zero** Go quota.
- Go is for `gpt-5.6-luna` reasoning and for what exists only there.
- Never spend Go on grep, file reads, exploration or mechanical test runs. Use
  the `explore` subagent and Ollama.
- Go limits are per model: 5-hour 20% / weekly 50% / monthly 100% of a monthly
  dollar cap. Track spend with `opencode stats --days 30 --models 20`.

## Mandatory pre-commit double red-team

This is a hard gate, separate from and cumulative to the protocol in `AGENTS.md`
(one `general` subagent validator before, gpt-5.6-sol via Codex after).

**Before any commit, without exception:**

1. `ollama-cloud/glm-5.3-flash` reviews the exact diff about to be committed.
2. `opencode/union-alpha` reviews the **same** diff, **independently** - do not
   show the first model's analysis to the second before it has concluded.

It holds even when another agent already reviewed, when you reviewed, when tests
are green, and when the change is trivial. The extra cost is intentional.

Flow: prepare the diff -> run the relevant tests -> send diff + context to both
-> compare findings -> investigate every plausible alert -> fix real defects ->
re-run tests -> if the fix changed the diff materially, run the double review
again -> only then commit.

They fail differently, so disagreements are signal, not noise. **Do not vote.**
Reproduce, read the code, run a test, get evidence, then decide as orchestrator.
Never commit while a plausible finding is unresolved and unrefuted.

Prompt both to look for: bugs, regressions, logic inconsistency, unhandled edge
cases, NaN/null/None behaviour, race conditions, state errors, persistence
issues, wrong assumptions, live/backtest/ML divergence, leakage and look-ahead
bias, arithmetic and unit errors, inverted signals, security and secret
exposure, auth errors, out-of-scope edits, and weak tests that pass without
checking behaviour.

## Treating agent output as evidence

Never accept a result on trust. Read the diff, judge the technical decisions,
run the tests yourself, check for regressions and out-of-scope edits, and confirm
the tests actually assert behaviour. Look for solutions more complex than the
problem. Reproduce before you believe.

## Handing off context

Send the goal, the relevant files, the constraints, the related tests, and the
necessary context. Do not dump the whole repository - it wastes quota and
contaminates context. When an agent already has a session for that task, resume
it instead of starting a fresh one without reason.
