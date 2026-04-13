# Evo Comparison Notes

Comparison of [evo-hq/evo](https://github.com/evo-hq/evo) with foray. Both are autonomous exploration tools dispatching Claude agents into git worktrees. Evo optimizes a quantitative metric; foray explores qualitative questions. Reviewed 2026-04-12.

## Worth borrowing

### 1. Discarded hypothesis tracking

Evo tracks which approaches were tried and failed, deduplicates them, and surfaces them to agents. This prevents re-exploring dead ends.

**Foray gap**: When a planner proposes an experiment, it sees prior findings but doesn't have a structured "these specific hypotheses were already disproven" list. An agent could easily propose a variation of something that already failed.

**Concrete change**: Add `discarded_hypotheses: list[str]` to PathInfo (or a separate file per path). When evaluator marks something FAILED or INFEASIBLE, extract the hypothesis and add it. Feed this list into planner context.

**Effort**: Small. **Impact**: High — directly prevents wasted budget.

### 2. Gate / regression guard inheritance

Evo accumulates "gates" (validation checks) through experiment ancestry. When an experiment confirms a finding, future experiments must pass all ancestor gates before committing.

**Foray gap**: The evaluator can mark a path RESOLVED, but there's no mechanism to ensure future experiments on related paths don't contradict confirmed findings. The synthesizer catches inconsistencies after the fact, but earlier prevention would save budget.

**Concrete change**: When a finding is marked SUCCESS with high confidence, generate a one-line assertion. Feed accumulated assertions into executor context as "known truths — your results must not contradict these."

**Effort**: Medium. **Impact**: Medium — prevents contradictory findings.

### 3. Real-time dashboard

Evo has a Flask web UI with live experiment graph, score progression scatter plot, and per-task traces. Auto-refreshes every 5 seconds.

**Foray gap**: `foray status` is a one-shot CLI snapshot. For 8-hour overnight runs, a live dashboard would be significantly better — especially an experiment tree visualization and finding progression.

**Concrete change**: Add `foray dashboard` using Flask or similar. Expose endpoints for run state, findings timeline, path status, and round outcomes. Even a minimal version (no scatter plot) would be high value.

**Effort**: Medium. **Impact**: High for long runs, low for short runs.

### 4. Experiment DAG vs. flat rounds

Evo structures experiments as a directed acyclic graph where new experiments can fork from any successful node. This enables tree-search exploration rather than foray's linear round-robin.

**Foray gap**: Round-based scheduling means each path gets one experiment per round regardless of how promising prior results were. A breakthrough finding on path A still waits for paths B and C before getting a follow-up.

**Assessment**: Deep architectural change — not something to bolt on. Worth considering for v0.3. The key insight: let evaluation results influence scheduling priority within a round, not just between rounds. A lighter version: let the scheduler weight paths by most recent evaluation confidence, so hot paths get more concurrent experiments.

**Effort**: Large. **Impact**: High — fundamentally better exploration.

### 5. Per-experiment annotations

Evo agents annotate experiments with structured analysis (patterns observed, untried suggestions, infrastructure notes). These annotations persist and feed into future context.

**Foray gap**: Findings have `summary` and `one_liner`, but no structured "what I observed but didn't pursue" field. Useful signal gets lost between rounds.

**Concrete change**: Add `observations: list[str]` and `suggested_next: list[str]` to Finding or ExperimentResult. Feed these into planner context for the same path.

**Effort**: Small. **Impact**: Medium — preserves agent insights between rounds.

## Not worth borrowing

- **Score-based commit/discard**: Evo optimizes a quantitative metric (higher/lower score). Foray explores qualitative questions — there's no numeric score to compare. The evaluator-as-judge pattern is the right fit.
- **SDK instrumentation**: Evo needs users to instrument their benchmark code. Foray's agents read and analyze the codebase directly. Different problem shape.
- **Advisory file locks (fcntl.flock)**: Foray's ThreadPoolExecutor + threading.Lock is sufficient since all concurrency is within a single process. File locks would add complexity for no gain unless foray moves to multi-process.
- **20+ CLI subcommands**: Evo's granular CLI (`evo new`, `evo run`, `evo discard`, `evo gate`) reflects its interactive workflow. Foray is fire-and-forget autonomous — the current 4 commands (`run`, `status`, `report`, `resume`) are the right surface.

## Priority ranking

| Item | Effort | Impact |
|------|--------|--------|
| Discarded hypothesis tracking | Small | High |
| Per-experiment annotations | Small | Medium |
| Accumulated assertions ("gates lite") | Medium | Medium |
| Dashboard | Medium | High for long runs |
| DAG-based scheduling | Large | High |
