# Strategist Agent: Vision-Level Feedback Loop

## Problem

Foray's vision drives initialization but not execution. Once the initializer decomposes the vision into paths, the system becomes a path-closing machine with no feedback loop to the original vision. If paths go narrow and deep without advancing the vision, nothing detects it. There is no mechanism to spawn new paths mid-run, detect vision gaps, or reprioritize based on what's been learned.

## Solution

A strategist agent that runs between rounds, reflecting on all path progress against the vision and making steering decisions. This creates the compounding reflection loop where each round's strategy builds on the last, driving convergence toward the vision rather than just exhausting paths.

## Architecture

### Position in the Loop

```
init → [round: plan → execute → evaluate] → strategist → [next round: ...] → synthesize
```

After each round's results are merged into state, before the next round begins, the orchestrator dispatches the strategist. The strategist is the only agent with a full-run view — planners see one path, evaluators see one experiment, but the strategist sees all paths, all findings, and the vision together.

### Model

Runs on `config.evaluator_model` (Opus by default). Same rationale as the evaluator: high-judgment, low-token-volume role where quality of reasoning directly determines run quality.

### Skip Conditions

The strategist does NOT run when:

- **Round 1** — the initializer just created the paths, nothing to reflect on yet.
- **1 experiment remaining in budget** — no point strategizing, just finish.
- **No open paths** — nothing to steer.

## Decisions

The strategist can make three types of decisions:

### Close Paths

Mark an open path as `inconclusive` or `resolved` with a reason. Used when a path isn't advancing the vision regardless of its experimental status. The evaluator says "this path's hypothesis is still open"; the strategist says "it doesn't matter, we've learned enough here to move on."

Constraint: the strategist cannot override the evaluator on resolved paths. If the evaluator marked a path resolved (with evidence), the strategist cannot reopen it. But it can close paths the evaluator left open.

### Open New Paths

Propose new `PathInfo` objects (id, description, priority, hypothesis). These get added to `paths.json` and become available for the next round's scheduling. Used when findings reveal unexplored dimensions of the vision, or when closed paths need replacement.

No hard cap on total paths. The initializer's 2-5 range is a soft guideline in the prompt. Budget and time constraints naturally prevent runaway path proliferation.

### Reprioritize

Change priority of existing open paths. Used when what's been learned shifts which path would most advance the vision.

## Output Format

The strategist writes `state/strategy.json` (overwritten each round):

```json
{
  "vision_assessment": "Brief reflection on how well current progress addresses the vision",
  "decisions": [
    {"action": "close", "path_id": "path-a", "status": "inconclusive", "reason": "..."},
    {"action": "open", "new_path": {"id": "new-path", "description": "...", "priority": "high", "hypothesis": "..."}},
    {"action": "reprioritize", "path_id": "path-b", "priority": "high"}
  ],
  "rationale": "Why these changes best advance the vision"
}
```

The `vision_assessment` is the continuity mechanism — it gets fed back into the next round's strategist context so each reflection builds on the last.

## Data Model

```python
class StrategyDecision(BaseModel):
    action: str  # "close", "open", "reprioritize"
    path_id: str = ""
    status: PathStatus | None = None  # for "close": inconclusive or resolved
    reason: str = ""
    priority: Priority | None = None
    new_path: PathInfo | None = None

class StrategyOutput(_AgentOutput):
    vision_assessment: str
    decisions: list[StrategyDecision] = Field(default_factory=list)
    rationale: str = ""
```

`StrategyOutput` extends `_AgentOutput` for null coercion since the strategist writes JSON.

## Context

`build_strategist_context()` provides:

- Full vision document
- All paths with current status, experiment counts, discarded hypotheses
- All findings (progressive summarization: last 3 per path get full summaries, older get one-liners)
- Previous `vision_assessment` from last round's strategy (if any)
- Budget remaining (experiments, time)

Token budget: ~20k (similar to evaluator). Input is summaries, not raw results. Output is a small JSON blob.

## Prompt Design

The prompt frames the strategist as a research director doing a periodic review:

1. **Reflect on vision progress** — What parts of the vision are being addressed? What parts are being neglected? Are the current paths converging toward answering the vision, or going deep on tangents?

2. **Evaluate each open path strategically** — Not experimentally (that's the evaluator's job), but strategically. Is continued work on this path the best use of remaining budget toward the vision?

3. **Identify gaps** — What questions does the vision raise that no current path addresses? Did findings reveal new dimensions worth exploring?

4. **Make decisions** — Close, open, reprioritize. Every decision must reference the vision.

5. **Be conservative** — Don't churn paths every round. Sustained exploration has value. Only intervene when the current trajectory is clearly not converging on the vision. Making no changes is often the right call.

## Orchestrator Integration

### Flow Change

In `_run_round()`, after all experiments in the round are merged:

1. Check skip conditions (round 1, last experiment, no open paths)
2. Build strategist context
3. Dispatch strategist agent
4. Parse `StrategyOutput` from result
5. Apply decisions to `paths.json`:
   - Close: set path status to the specified value
   - Open: append new `PathInfo` to paths list
   - Reprioritize: update path priority
6. Write strategy to `state/strategy.json`
7. Log decisions for progress output

### Timing

Record strategist dispatch in timing.jsonl like all other agents (`agent_type: "strategist"`).

## Downstream: Planner Context Enhancement

The strategist's `vision_assessment` flows into `build_planner_context()`:

- Append the latest `vision_assessment` after the path info section
- This gives the planner vision-informed context without requiring it to do its own vision analysis
- The planner doesn't see other paths' decisions, just the assessment relevant to planning

This closes the feedback loop: vision → initializer → paths → experiments → evaluator → strategist reflects on vision → planner gets that reflection → next experiment is vision-informed.

## Files to Create/Modify

- `src/foray/models.py` — Add `StrategyDecision`, `StrategyOutput`
- `src/foray/agents/strategist.md` — New agent prompt
- `src/foray/context.py` — Add `build_strategist_context()`, update `build_planner_context()` with vision_assessment
- `src/foray/state.py` — Add `write_strategy()`, `read_strategy()`
- `src/foray/orchestrator.py` — Add strategist dispatch after round merge, apply decisions
- `tests/test_models.py` — StrategyDecision, StrategyOutput tests
- `tests/test_context.py` — Strategist context and planner enhancement tests
- `tests/test_state.py` — Strategy read/write tests
- `tests/test_orchestrator.py` — Strategist dispatch and decision application tests
