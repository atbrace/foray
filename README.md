# Foray

Autonomous exploration tool that dispatches Claude Code agents to run experiments in isolated git worktrees and produces a synthesis report. Point it at a codebase with a question, run it overnight, come back to findings.

## How It Works

1. **You provide a vision** -- a question or document describing what you want to explore
2. **Foray identifies exploration paths** -- independent questions worth investigating
3. **For each path, agents run experiments** -- plan, execute in an isolated worktree, evaluate results
4. **A synthesis report** ties everything together when the budget runs out

```
Vision doc ──► Initializer ──► Paths
                                 │
                    ┌────────────┘
                    ▼
              ┌─ Planner ──► Experiment Plan
    Round N ──┤  Executor ──► Results (in worktree)
              └─ Evaluator ──► Assessment + Path Status
                    │
                    ▼
              Next round (repeat until budget exhausted)
                    │
                    ▼
              Synthesizer ──► Final Report
```

Experiments run in isolated git worktrees so agents can make changes freely without affecting your working tree. Destructive git operations (push, branch delete, remote modify) are blocked by a git wrapper.

## Install

Requires Python 3.12+ and the [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated.

```bash
# Install from source
uv tool install /path/to/foray

# Or from the repo
git clone <repo-url> && cd foray
uv tool install .
```

## Usage

```bash
# Quick exploration with an inline question
foray run --question "What testing patterns does this codebase use?" --hours 0.5 --max-experiments 3

# Deeper exploration with a vision document
foray run --vision VISION.md --hours 8

# Check progress while it runs
foray status

# Read the final report
foray report

# Resume after a crash or Ctrl+C
foray resume
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--vision` | | Path to vision document (markdown) |
| `--question` | | Inline exploration question |
| `--hours` | 8.0 | Time budget |
| `--max-experiments` | 50 | Experiment cap |
| `--model` | claude-sonnet-4-6 | Model for most agents |
| `--evaluator-model` | claude-opus-4-6 | Model for evaluator (higher reasoning) |
| `--max-turns` | 30 | Max tool-use turns per agent |
| `--allow` | | Additional tools to enable (repeatable) |
| `--deny` | | Tools to disable (repeatable) |

### Vision Documents

A vision doc is a markdown file describing what you want to explore. Focus on what you're stuck on and what you'd like to learn:

```markdown
My goal is to understand how the authentication system works and whether
it can be extended to support SSO. Currently, login is username/password
only and the session handling seems fragile.

I'd like to know:
- How sessions are managed and where state lives
- Whether the auth module is decoupled enough to swap providers
- What would break if we added OAuth/SAML
```

### Stopping a Run

- **Ctrl+C** -- kills the current agent and stops the run
- **Touch `.foray/.stop`** -- graceful stop after the current experiment finishes

## Architecture

- **Python orchestrator** handles bookkeeping: state management, scheduling, worktree lifecycle, guardrails
- **Claude Code CLI agents** do the thinking: explore code, write experiments, evaluate results
- **State on disk** as structured JSON in `.foray/` -- survives crashes, enables resume
- **Agents are stateless** -- they read context, do work, write output, and die. No inter-agent communication except through the orchestrator's state files

### Agents

| Agent | Model | Purpose |
|-------|-------|---------|
| Initializer | Sonnet | Scans codebase, reads vision, identifies 3-5 exploration paths |
| Planner | Sonnet | Designs a specific experiment for a path |
| Executor | Sonnet | Runs the experiment in an isolated worktree |
| Evaluator | **Opus** | Assesses results, recommends path status, rates confidence |
| Synthesizer | Sonnet | Produces final report from all findings |

The evaluator uses Opus by default because its judgment calls steer the entire run -- bad evaluations waste budget on dead ends or prematurely close promising paths.

### Safety

- Experiments run in detached-HEAD git worktrees
- A git wrapper intercepts and blocks `push`, `branch -D`, and `remote remove`
- Git integrity checks verify HEAD and branches haven't changed after each experiment
- The orchestrator applies deterministic guardrails on top of evaluator recommendations (e.g., requires 2+ successful experiments before resolving a path)

## Development

```bash
# Run tests
uv run pytest -v

# Reinstall after changes (--reinstall forces rebuild from source)
uv tool install /path/to/foray --force --reinstall
```
