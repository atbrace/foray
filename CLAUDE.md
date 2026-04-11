# Foray

## Project Overview

Foray is an autonomous exploration tool that dispatches Claude Code CLI agents to run experiments in isolated git worktrees and produces a synthesis report. Point it at a codebase with a question, run it overnight, come back to findings.

- Design doc: `2026-04-10-foray-v3-design.md`
- Implementation plan: `docs/superpowers/plans/2026-04-10-foray-v3.md`
- Architecture: Python orchestrator (reliable bookkeeping) dispatching stateless Claude Code CLI agents (the intelligence). State lives on disk as structured JSON. Agents read state, do work, write results, and die.

## Status

v0.1.0 — fully implemented and installed globally (`foray` on PATH via `uv tool install`).

84 tests passing across 11 test modules. All modules from the implementation plan are complete.

## Quick Start

```bash
# From any project directory:
foray run --question "What testing patterns does this codebase use?" --hours 0.5 --max-experiments 3

# Or with a vision document:
foray run --vision VISION.md --hours 8

# Check progress:
foray status

# Read the report:
foray report

# Resume a crashed/stopped run:
foray resume
```

Requires `claude` CLI installed and authenticated (dispatches agents via `claude -p`).

## Module Map

```
src/foray/
├── models.py        # Pydantic models: PathInfo, Round, Finding, Evaluation, RunState, etc.
│                    # PathInfo and Evaluation extend _AgentOutput (coerces null → defaults)
├── state.py         # Atomic JSON read/write, .foray/ directory initialization
├── permissions.py   # Default tool list + --allow/--deny resolution
├── scheduler.py     # Round-robin path assignment, concentration detection, circuit breakers
├── worktree.py      # Git worktree create/cleanup/preserve, git wrapper, integrity checks
│                    # create_worktree prunes stale git references before creating
├── context.py       # Progressive summarization, scoped context per agent type, token estimation
├── dispatcher.py    # Claude Code CLI subprocess invocation, timeout, crash stubs
│                    # Crash stubs capture agent stdout for diagnostics
├── orchestrator.py  # Main loop: init → rounds → synthesis, state transition guardrails
│                    # Timestamped progress output at every stage transition
├── cli.py           # Click CLI: run, report, status, resume commands
└── agents/          # Bundled agent prompt markdown files
    ├── initializer.md   # Shallow codebase scan, 3-5 focused paths
    ├── planner.md
    ├── executor.md
    ├── evaluator.md     # Runs on Opus (evaluator_model) by default
    └── synthesizer.md
```

## Agent Models

- **Initializer, Planner, Executor, Synthesizer** — use `config.model` (default: `claude-sonnet-4-6`)
- **Evaluator** — uses `config.evaluator_model` (default: `claude-opus-4-6`). The evaluator makes judgment calls that steer the entire run (path resolution, blocking, confidence). Opus-level reasoning here prevents wasted budget from bad evaluations. Token cost is minimal since evaluator input/output is small.

## Tech Stack

- **Python 3.12+** — orchestrator language
- **pydantic v2** — data models, JSON serialization/validation
- **click** — CLI framework
- **pytest** — test framework
- **uv** — package management, virtualenv, running (`uv run`, `uv sync`, `uv pip`)
- **hatchling** — build backend

These are locked decisions. Do not introduce alternative libraries for the same purpose.

## Reinstalling After Changes

```bash
uv tool install /Users/austinbrace/Developer/foray --force --reinstall
```

`--reinstall` is required to force a rebuild from source. `--force` alone reuses cached wheels and won't pick up code changes.

## Development Workflow

Every task follows this cycle:

1. Write failing tests (red)
2. Write minimal implementation to pass (green)
3. Refactor while tests stay green (refactor)
4. Run full test suite: `uv run pytest -v`
5. Run `/simplify` on changed code
6. Run code-review agent on changed code
7. Commit with descriptive message

Subagents must complete steps 4-7 before marking any task done.

The project lives in a private GitHub repo. Push after each task's commit.

## Code Principles

- **No fallbacks.** Do not create fallback code paths. If something fails, it should fail visibly. Two code paths means two things to maintain and a place for bugs to hide.
- **No silent error swallowing.** Never write empty `except` blocks. Never catch exceptions without logging or re-raising. This is the #1 lesson from Scout v2's 30% silent failure rate.
- **No guessing external interfaces.** Do not assume Claude Code CLI flags, subprocess behavior, or API shapes. Verify by running `claude --help`, reading docs, or testing. If you can't verify, leave a `# TODO: verify` comment and flag it.
- **Defend against LLM output shapes.** Models that write JSON will emit `null` for fields they consider absent, even when the schema expects a concrete default. Any Pydantic model deserialized from agent-written JSON must extend `_AgentOutput` to coerce nulls. Test with `model_validate_json` using realistic agent output (including nulls and missing fields), not just Python kwargs.
- **Atomic writes for all state.** Every JSON state mutation goes through `_atomic_write` (temp file + rename). No direct `path.write_text()` for state files.
- **YAGNI.** Do not add features, configuration options, or abstractions not in the design doc. The design doc is the scope.
- **Orchestrator owns all state mutations.** Agents write only their own output files. Shared state (`paths.json`, `rounds.json`) is only modified by the orchestrator through `state.py`.

## Testing Rules

- **Red-green-refactor.** Write the failing test first. Watch it fail. Write the minimum code to pass. Refactor only while tests stay green. No exceptions.
- **Run full suite before commit.** `uv run pytest -v` must pass with zero failures.
- **Don't mock what you can test directly.** Use `tmp_path` fixtures and real git repos (via `conftest.py`'s `git_repo` fixture) for state and worktree tests. Reserve mocking for `subprocess.run` in dispatcher tests where invoking Claude Code CLI is impractical.
- **Test behavior, not implementation.** Tests assert on observable outcomes (files written, status returned, state changed), not internal function calls.

## Agent Prompt Authoring

- **Exact output formats with examples.** Every agent prompt specifies the exact file format and structure it must produce. Include a JSON/markdown example in the prompt — don't rely on the agent inferring the format.
- **Every prompt constraint must have enforcement outside the prompt.** If a prompt says "don't push to remote," the git wrapper and integrity check enforce it. Prompt instructions are informational, not enforcement. If you add a constraint to a prompt, verify there's a mechanism backing it up.
- **Test prompts empirically.** Before committing a new or modified agent prompt, run at least one manual dispatch (`claude -p` with the prompt) against a test project and verify the output matches the expected format. Prompt quality is iterated empirically, not theoretically.

## Subagent Discipline

- **Stay within task scope.** Each task in the implementation plan lists specific files. Do not modify files outside that list.
- **No drive-by improvements.** Do not refactor surrounding code, add docstrings to code you didn't write, or "improve" things beyond the task specification.
- **Mark complete only after verification.** A task is done when: tests pass, /simplify has run, code-review agent has run, and changes are committed. Not before.
- **Flag blockers, don't work around them.** If a task requires something from a prior task that's missing or broken, stop and report. Do not invent workarounds.
