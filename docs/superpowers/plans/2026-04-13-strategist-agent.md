# Strategist Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a strategist agent that runs between rounds to create a vision-level feedback loop, enabling mid-run path creation, closure, and reprioritization based on vision convergence.

**Architecture:** New `StrategyOutput` model holds decisions. `build_strategist_context()` assembles vision + all paths + findings + previous assessment. Orchestrator dispatches strategist after each round's merge phase, applies decisions to `paths.json`. Planner context enhanced with latest `vision_assessment`.

**Tech Stack:** Python, Pydantic v2, Click (progress output), Claude Code CLI (dispatch)

**Spec:** `docs/superpowers/specs/2026-04-13-strategist-agent-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/foray/models.py` | Modify (after line 186) | Add `StrategyDecision` and `StrategyOutput` models |
| `src/foray/state.py` | Modify (after line 149) | Add `write_strategy()` and `read_strategy()` |
| `src/foray/context.py` | Modify (add function, modify `build_planner_context`) | Add `build_strategist_context()`, inject `vision_assessment` into planner |
| `src/foray/orchestrator.py` | Modify (after round merge in `run()`, imports) | Add `_run_strategist()` and `_apply_strategy()`, call after merge |
| `src/foray/agents/strategist.md` | Create | Strategist agent prompt |
| `tests/test_models.py` | Modify | Tests for new models |
| `tests/test_state.py` | Modify | Tests for strategy read/write |
| `tests/test_context.py` | Modify | Tests for strategist context and planner enhancement |
| `tests/test_orchestrator.py` | Modify | Tests for strategist dispatch and decision application |

---

### Task 1: StrategyDecision and StrategyOutput Models

**Files:**
- Modify: `src/foray/models.py` (after line 186, end of file)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write failing tests**

Add to the end of `tests/test_models.py`:

```python
# --- Strategist models ---


def test_strategy_decision_close():
    from foray.models import StrategyDecision, PathStatus
    d = StrategyDecision(action="close", path_id="path-a", status=PathStatus.INCONCLUSIVE, reason="Not advancing vision")
    assert d.action == "close"
    assert d.path_id == "path-a"
    assert d.status == PathStatus.INCONCLUSIVE
    assert d.reason == "Not advancing vision"


def test_strategy_decision_open():
    from foray.models import StrategyDecision, PathInfo, Priority
    new_path = PathInfo(id="path-new", description="New exploration", priority=Priority.HIGH, hypothesis="New hyp")
    d = StrategyDecision(action="open", new_path=new_path)
    assert d.action == "open"
    assert d.new_path.id == "path-new"


def test_strategy_decision_reprioritize():
    from foray.models import StrategyDecision, Priority
    d = StrategyDecision(action="reprioritize", path_id="path-b", priority=Priority.HIGH)
    assert d.action == "reprioritize"
    assert d.priority == Priority.HIGH


def test_strategy_decision_defaults():
    from foray.models import StrategyDecision
    d = StrategyDecision(action="close")
    assert d.path_id == ""
    assert d.status is None
    assert d.reason == ""
    assert d.priority is None
    assert d.new_path is None


def test_strategy_output_basic():
    from foray.models import StrategyOutput
    s = StrategyOutput(vision_assessment="Good progress on path-a", rationale="Stay the course")
    assert s.vision_assessment == "Good progress on path-a"
    assert s.decisions == []
    assert s.rationale == "Stay the course"


def test_strategy_output_null_coercion():
    """StrategyOutput extends _AgentOutput — null fields coerce to defaults."""
    import json
    from foray.models import StrategyOutput
    raw = json.dumps({
        "vision_assessment": "test",
        "decisions": None,
        "rationale": None,
    })
    s = StrategyOutput.model_validate_json(raw)
    assert s.decisions == []
    assert s.rationale == ""


def test_strategy_output_with_decisions():
    import json
    from foray.models import StrategyOutput
    raw = json.dumps({
        "vision_assessment": "Path-a is stale",
        "decisions": [
            {"action": "close", "path_id": "path-a", "status": "inconclusive", "reason": "Not advancing"},
            {"action": "open", "new_path": {
                "id": "path-c", "description": "Fresh angle", "priority": "high", "hypothesis": "New hyp"
            }},
            {"action": "reprioritize", "path_id": "path-b", "priority": "high"},
        ],
        "rationale": "Pivoting to fresh approach",
    })
    s = StrategyOutput.model_validate_json(raw)
    assert len(s.decisions) == 3
    assert s.decisions[0].action == "close"
    assert s.decisions[0].status == "inconclusive"
    assert s.decisions[1].new_path.id == "path-c"
    assert s.decisions[2].priority == "high"


def test_strategy_output_roundtrip():
    import json
    from foray.models import StrategyOutput, StrategyDecision, PathStatus, Priority
    s = StrategyOutput(
        vision_assessment="test",
        decisions=[
            StrategyDecision(action="close", path_id="a", status=PathStatus.INCONCLUSIVE, reason="done"),
            StrategyDecision(action="reprioritize", path_id="b", priority=Priority.LOW),
        ],
        rationale="reason",
    )
    raw = s.model_dump_json()
    s2 = StrategyOutput.model_validate_json(raw)
    assert s2.vision_assessment == "test"
    assert len(s2.decisions) == 2
    assert s2.decisions[0].status == PathStatus.INCONCLUSIVE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -k "strategy" -v`
Expected: ImportError — `StrategyDecision` and `StrategyOutput` don't exist yet.

- [ ] **Step 3: Write implementation**

Add to the end of `src/foray/models.py` (after line 186):

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -k "strategy" -v`
Expected: All 9 strategy tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foray/models.py tests/test_models.py
git commit -m "feat: add StrategyDecision and StrategyOutput models"
```

---

### Task 2: Strategy State Persistence

**Files:**
- Modify: `src/foray/state.py` (after line 149, add functions; add import at line 14)
- Test: `tests/test_state.py`

- [ ] **Step 1: Write failing tests**

Add to the end of `tests/test_state.py`:

```python
# --- Strategy persistence ---


def test_write_and_read_strategy(tmp_path):
    from foray.models import StrategyOutput, StrategyDecision
    from foray.state import write_strategy, read_strategy
    foray_dir = tmp_path
    (foray_dir / "state").mkdir()

    strategy = StrategyOutput(
        vision_assessment="Good progress",
        decisions=[StrategyDecision(action="close", path_id="a", reason="done")],
        rationale="Staying focused",
    )
    write_strategy(foray_dir, strategy)
    loaded = read_strategy(foray_dir)
    assert loaded is not None
    assert loaded.vision_assessment == "Good progress"
    assert len(loaded.decisions) == 1
    assert loaded.decisions[0].path_id == "a"


def test_read_strategy_missing(tmp_path):
    from foray.state import read_strategy
    foray_dir = tmp_path
    (foray_dir / "state").mkdir()
    assert read_strategy(foray_dir) is None


def test_read_strategy_malformed(tmp_path):
    from foray.state import read_strategy
    foray_dir = tmp_path
    (foray_dir / "state").mkdir()
    (foray_dir / "state" / "strategy.json").write_text("not json{{{")
    assert read_strategy(foray_dir) is None


def test_write_strategy_overwrites(tmp_path):
    from foray.models import StrategyOutput
    from foray.state import write_strategy, read_strategy
    foray_dir = tmp_path
    (foray_dir / "state").mkdir()

    write_strategy(foray_dir, StrategyOutput(vision_assessment="first"))
    write_strategy(foray_dir, StrategyOutput(vision_assessment="second"))
    loaded = read_strategy(foray_dir)
    assert loaded.vision_assessment == "second"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_state.py -k "strategy" -v`
Expected: ImportError — `write_strategy` and `read_strategy` don't exist yet.

- [ ] **Step 3: Write implementation**

In `src/foray/state.py`, add `StrategyOutput` to the import block at line 14:

```python
from foray.models import (
    Evaluation,
    Finding,
    PathInfo,
    Round,
    RunState,
    StrategyOutput,
    TimingRecord,
)
```

Add after `read_timing` (after line 149):

```python
def write_strategy(foray_dir: Path, strategy: StrategyOutput) -> None:
    _atomic_write(foray_dir / "state" / "strategy.json", strategy.model_dump_json(indent=2))


def read_strategy(foray_dir: Path) -> StrategyOutput | None:
    path = foray_dir / "state" / "strategy.json"
    if not path.exists():
        return None
    try:
        return StrategyOutput.model_validate_json(path.read_text())
    except (ValidationError, ValueError) as e:
        logger.warning(f"Failed to parse strategy: {e}")
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_state.py -k "strategy" -v`
Expected: All 4 strategy state tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foray/state.py tests/test_state.py
git commit -m "feat: add strategy state persistence (write/read)"
```

---

### Task 3: Strategist Context Builder

**Files:**
- Modify: `src/foray/context.py` (add function, add to `BUDGETS`, add import)
- Test: `tests/test_context.py`

- [ ] **Step 1: Write failing tests**

Add to the end of `tests/test_context.py`:

```python
# --- Strategist context ---


def test_strategist_context_includes_vision(tmp_path: Path):
    from foray.context import build_strategist_context
    from foray.models import StrategyOutput
    (tmp_path / "vision.md").write_text("Explore testing patterns")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "paths.json").write_text("[]")
    (tmp_path / "state" / "findings.json").write_text("[]")
    (tmp_path / "experiments").mkdir()

    ctx = build_strategist_context(tmp_path, _state(), previous_assessment=None)
    assert "Explore testing patterns" in ctx


def test_strategist_context_includes_all_paths(tmp_path: Path):
    from foray.context import build_strategist_context
    from foray.state import write_paths, write_findings
    (tmp_path / "vision.md").write_text("Vision")
    (tmp_path / "state").mkdir()
    (tmp_path / "experiments").mkdir()

    paths = [_path("path-a"), _path("path-b")]
    write_paths(tmp_path, paths)
    write_findings(tmp_path, [])

    ctx = build_strategist_context(tmp_path, _state(), previous_assessment=None)
    assert "path-a" in ctx
    assert "path-b" in ctx


def test_strategist_context_includes_findings_by_path(tmp_path: Path):
    from foray.context import build_strategist_context
    from foray.state import write_paths, write_findings
    (tmp_path / "vision.md").write_text("Vision")
    (tmp_path / "state").mkdir()
    (tmp_path / "experiments").mkdir()

    write_paths(tmp_path, [_path("path-a")])
    write_findings(tmp_path, [
        _finding("001", "path-a"),
        _finding("002", "path-a"),
    ])

    ctx = build_strategist_context(tmp_path, _state(), previous_assessment=None)
    assert "001" in ctx
    assert "002" in ctx


def test_strategist_context_includes_previous_assessment(tmp_path: Path):
    from foray.context import build_strategist_context
    (tmp_path / "vision.md").write_text("Vision")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "paths.json").write_text("[]")
    (tmp_path / "state" / "findings.json").write_text("[]")
    (tmp_path / "experiments").mkdir()

    ctx = build_strategist_context(
        tmp_path, _state(),
        previous_assessment="Path-a is going deep but not advancing the core question",
    )
    assert "Path-a is going deep but not advancing the core question" in ctx


def test_strategist_context_no_previous_assessment(tmp_path: Path):
    from foray.context import build_strategist_context
    (tmp_path / "vision.md").write_text("Vision")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "paths.json").write_text("[]")
    (tmp_path / "state" / "findings.json").write_text("[]")
    (tmp_path / "experiments").mkdir()

    ctx = build_strategist_context(tmp_path, _state(), previous_assessment=None)
    assert "Previous Vision Assessment" not in ctx


def test_strategist_context_includes_budget(tmp_path: Path):
    from foray.context import build_strategist_context
    (tmp_path / "vision.md").write_text("Vision")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "paths.json").write_text("[]")
    (tmp_path / "state" / "findings.json").write_text("[]")
    (tmp_path / "experiments").mkdir()

    ctx = build_strategist_context(tmp_path, _state(), previous_assessment=None)
    assert "50 experiments" in ctx  # from default RunConfig.max_experiments
    assert "8.0 hours" in ctx  # from default RunConfig.hours
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_context.py -k "strategist" -v`
Expected: ImportError — `build_strategist_context` doesn't exist yet.

- [ ] **Step 3: Write implementation**

In `src/foray/context.py`, add `StrategyOutput` to the import from `foray.state`:

```python
from foray.state import read_evaluation, read_findings, read_paths, read_strategy
```

Add `"strategist"` to `BUDGETS`:

```python
BUDGETS = {
    "planner": 30_000,
    "executor": 15_000,
    "evaluator": 20_000,
    "strategist": 20_000,
    "synthesizer": 60_000,
}
```

Add after `build_exhaustion_evaluator_context` (after line 298):

```python
def build_strategist_context(
    foray_dir: Path,
    run_state: RunState,
    previous_assessment: str | None,
) -> str:
    """Strategist context: vision + all paths + findings + budget + previous assessment."""
    vision = _read_file(foray_dir / "vision.md")
    paths = read_paths(foray_dir)
    findings = read_findings(foray_dir)

    # Group findings by path
    by_path: dict[str, list[Finding]] = {}
    for f in findings:
        by_path.setdefault(f.path_id, []).append(f)

    sections = [f"# Vision\n\n{vision}"]

    if previous_assessment:
        sections.append(f"\n# Previous Vision Assessment\n\n{previous_assessment}")

    # All paths with status and findings
    sections.append("\n# Paths")
    for p in paths:
        icon = _STATUS_ICONS.get(p.status.value, "·")
        sections.append(
            f"\n## {icon} {p.id} ({p.status.value})\n"
            f"**Description:** {p.description}\n"
            f"**Priority:** {p.priority}\n"
            f"**Hypothesis:** {p.hypothesis}\n"
            f"**Experiments:** {p.experiment_count}"
        )
        if p.discarded_hypotheses:
            sections.append(f"**Discarded approaches:** {'; '.join(p.discarded_hypotheses)}")

        path_findings = by_path.get(p.id, [])
        if path_findings:
            recent = path_findings[-3:]
            older = path_findings[:-3]
            if older:
                for f in older:
                    sections.append(f"- {f.experiment_id}: [{f.status}] {f.one_liner}")
            for f in recent:
                brief = f.planner_brief if f.planner_brief else f.summary
                sections.append(f"- {f.experiment_id}: [{f.status}] {brief}")

    # Budget info
    remaining_experiments = run_state.config.max_experiments - run_state.experiment_count
    sections.append(
        f"\n# Budget\n"
        f"- Round: {run_state.current_round}\n"
        f"- Experiments completed: {run_state.experiment_count}\n"
        f"- Experiments remaining: {remaining_experiments}\n"
        f"- Total budget: {run_state.config.max_experiments} experiments, "
        f"{run_state.config.hours} hours"
    )

    context = "\n".join(sections)
    tokens = estimate_tokens(context)
    if tokens > BUDGETS["strategist"]:
        # Truncate: reduce all findings to one-liners
        sections_truncated = [f"# Vision\n\n{vision}"]
        if previous_assessment:
            sections_truncated.append(f"\n# Previous Vision Assessment\n\n{previous_assessment}")
        sections_truncated.append("\n# Paths (summarized)")
        for p in paths:
            icon = _STATUS_ICONS.get(p.status.value, "·")
            sections_truncated.append(f"- {icon} {p.id} ({p.status.value}, {p.experiment_count} exp, {p.priority}): {p.hypothesis}")
            path_findings = by_path.get(p.id, [])
            for f in path_findings:
                sections_truncated.append(f"  - {f.experiment_id}: [{f.status}] {f.one_liner}")
        sections_truncated.append(
            f"\n# Budget\n- Round: {run_state.current_round}\n"
            f"- Remaining: {remaining_experiments} experiments\n"
            f"- Total: {run_state.config.max_experiments} experiments, {run_state.config.hours} hours"
        )
        context = "\n".join(sections_truncated)
        logger.info(f"Strategist context truncated to ~{estimate_tokens(context)} tokens")
    return context
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_context.py -k "strategist" -v`
Expected: All 6 strategist context tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foray/context.py tests/test_context.py
git commit -m "feat: add build_strategist_context for vision-level review"
```

---

### Task 4: Planner Context Enhancement with Vision Assessment

**Files:**
- Modify: `src/foray/context.py` (`build_planner_context` function)
- Test: `tests/test_context.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_context.py`:

```python
# --- Planner vision assessment injection ---


def test_planner_includes_vision_assessment(tmp_path: Path):
    from foray.state import write_strategy
    from foray.models import StrategyOutput
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()
    (tmp_path / "state").mkdir()

    write_strategy(tmp_path, StrategyOutput(
        vision_assessment="Path-a is central to the vision, keep pushing",
    ))

    ctx = build_planner_context(tmp_path, _path(), [], _state(), needs_justification=False)
    assert "Path-a is central to the vision, keep pushing" in ctx


def test_planner_no_vision_assessment_when_no_strategy(tmp_path: Path):
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()
    (tmp_path / "state").mkdir()

    ctx = build_planner_context(tmp_path, _path(), [], _state(), needs_justification=False)
    assert "Vision Assessment" not in ctx
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_context.py -k "planner_includes_vision_assessment or planner_no_vision_assessment" -v`
Expected: FAIL — `build_planner_context` doesn't read strategy yet.

- [ ] **Step 3: Write implementation**

In `src/foray/context.py`, modify `build_planner_context`. After the path info section (after line 71, before the `discarded_hypotheses` check), add:

```python
    strategy = read_strategy(foray_dir)
    if strategy and strategy.vision_assessment:
        sections.append(f"\n## Vision Assessment\n{strategy.vision_assessment}")
```

This slots the vision assessment between the path info and the discarded hypotheses, giving the planner strategic context before it sees experiment history.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_context.py -k "planner_includes_vision_assessment or planner_no_vision_assessment" -v`
Expected: Both PASS.

- [ ] **Step 5: Run full context test suite**

Run: `uv run pytest tests/test_context.py -v`
Expected: All tests PASS (existing tests unaffected — they don't write strategy.json).

- [ ] **Step 6: Commit**

```bash
git add src/foray/context.py tests/test_context.py
git commit -m "feat: inject strategist vision_assessment into planner context"
```

---

### Task 5: Strategist Agent Prompt

**Files:**
- Create: `src/foray/agents/strategist.md`

- [ ] **Step 1: Write the prompt**

Create `src/foray/agents/strategist.md`:

```markdown
# Foray Strategist

You are a research director reviewing exploration progress after each round. Your job is to ensure the run converges on the vision, not just exhausts paths.

## Your Role

The planner sees one path. The evaluator sees one experiment. You see everything: the vision, all paths, all findings, and budget remaining. Use this full picture to steer the run toward answering the vision's core questions.

## Output

Write exactly this JSON structure to the specified path:

```json
{
  "vision_assessment": "2-4 sentence reflection on how well current progress addresses the vision. What's being covered? What's being neglected? Is the run converging or going sideways?",
  "decisions": [],
  "rationale": "1-2 sentences explaining why these changes (or no changes) best advance the vision"
}
```

## Decisions

Each decision is one of:

### Close a path

```json
{"action": "close", "path_id": "path-id", "status": "inconclusive", "reason": "Why this path should stop"}
```

Use `"inconclusive"` when the path isn't producing vision-relevant insights. Use `"resolved"` only if the path's question has been answered (even if the evaluator hasn't caught up).

**You cannot reopen paths the evaluator marked resolved.** Resolved paths have evidence — respect that. But you CAN close paths the evaluator left open.

### Open a new path

```json
{"action": "open", "new_path": {"id": "descriptive-id", "description": "What this path explores", "priority": "high|medium|low", "hypothesis": "Testable hypothesis"}}
```

Open new paths when:
- Findings reveal a dimension of the vision that no current path addresses
- A closed path needs replacement with a different angle
- The vision has an obvious gap that the initializer missed

Path IDs should be lowercase-kebab-case and descriptive (e.g., `streaming-api-latency`, not `path-4`).

### Reprioritize a path

```json
{"action": "reprioritize", "path_id": "path-id", "priority": "high|medium|low"}
```

Shift priority when what's been learned changes which path would most advance the vision.

## Decision Principles

1. **The vision is the north star.** Every decision must reference the vision. "Closing path-a because it's stale" is not enough — explain why the vision is better served by closing it.

2. **Be conservative.** Sustained exploration has value. Don't churn paths every round. Only intervene when the current trajectory is clearly not converging on the vision.

3. **No changes is often right.** If paths are progressing well toward the vision, output zero decisions. The worst outcome is thrashing — closing and opening paths every round so nothing goes deep enough.

4. **Budget awareness.** With 5 experiments remaining, don't open 3 new paths. Match ambition to remaining budget.

5. **Build on what's working.** If a path produced strong findings, consider whether those findings suggest a new path rather than just continuing the current one.

## The Key Question

Before writing your output, ask yourself: **"If I step back and look at everything we've learned so far, are we actually getting closer to answering the vision? Or are we going deep on tangents while the core question goes unaddressed?"**

If the answer is "tangents," that's when you intervene. If the answer is "converging," stay the course.

## Anti-Patterns

- **Don't close paths just because they're hard.** FAILED experiments that reveal real constraints are valuable.
- **Don't open paths for minor variations.** "Try the same thing with a different library" is not a new path — it's a new experiment on the existing path.
- **Don't reprioritize based on one experiment.** Wait for a pattern before shifting priorities.
- **Don't micromanage.** You steer the ship; the planner and evaluator handle individual experiments.
```

- [ ] **Step 2: Verify the prompt file exists**

Run: `ls -la src/foray/agents/strategist.md`
Expected: File exists.

- [ ] **Step 3: Commit**

```bash
git add src/foray/agents/strategist.md
git commit -m "feat: add strategist agent prompt"
```

---

### Task 6: Orchestrator Integration — Strategy Dispatch and Application

**Files:**
- Modify: `src/foray/orchestrator.py` (imports, new methods, modify `run()`)
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: Write failing tests**

Add to the end of `tests/test_orchestrator.py`:

```python
# --- Strategist integration ---


def test_apply_strategy_close_path(tmp_path):
    """_apply_strategy closes paths as directed by strategist."""
    from foray.models import StrategyOutput, StrategyDecision
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)

    from foray.state import write_paths, read_paths
    write_paths(foray_dir, [
        PathInfo(id="a", description="test", priority=Priority.HIGH, hypothesis="h", status=PathStatus.OPEN),
        PathInfo(id="b", description="test2", priority=Priority.MEDIUM, hypothesis="h2", status=PathStatus.OPEN),
    ])

    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir

    strategy = StrategyOutput(
        vision_assessment="Path a is not advancing vision",
        decisions=[StrategyDecision(action="close", path_id="a", status=PathStatus.INCONCLUSIVE, reason="stale")],
    )
    orch._apply_strategy(strategy)

    paths = read_paths(foray_dir)
    assert paths[0].id == "a"
    assert paths[0].status == PathStatus.INCONCLUSIVE
    assert paths[1].status == PathStatus.OPEN


def test_apply_strategy_open_path(tmp_path):
    """_apply_strategy adds new paths."""
    from foray.models import StrategyOutput, StrategyDecision
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)

    from foray.state import write_paths, read_paths
    write_paths(foray_dir, [
        PathInfo(id="a", description="test", priority=Priority.HIGH, hypothesis="h"),
    ])

    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir

    new_path = PathInfo(id="new-path", description="Fresh angle", priority=Priority.HIGH, hypothesis="new hyp")
    strategy = StrategyOutput(
        vision_assessment="Need new direction",
        decisions=[StrategyDecision(action="open", new_path=new_path)],
    )
    orch._apply_strategy(strategy)

    paths = read_paths(foray_dir)
    assert len(paths) == 2
    assert paths[1].id == "new-path"
    assert paths[1].status == PathStatus.OPEN


def test_apply_strategy_reprioritize(tmp_path):
    """_apply_strategy changes path priority."""
    from foray.models import StrategyOutput, StrategyDecision
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)

    from foray.state import write_paths, read_paths
    write_paths(foray_dir, [
        PathInfo(id="a", description="test", priority=Priority.LOW, hypothesis="h"),
    ])

    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir

    strategy = StrategyOutput(
        vision_assessment="a is now critical",
        decisions=[StrategyDecision(action="reprioritize", path_id="a", priority=Priority.HIGH)],
    )
    orch._apply_strategy(strategy)

    paths = read_paths(foray_dir)
    assert paths[0].priority == Priority.HIGH


def test_apply_strategy_skips_resolved_paths(tmp_path):
    """_apply_strategy refuses to close evaluator-resolved paths."""
    from foray.models import StrategyOutput, StrategyDecision
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)

    from foray.state import write_paths, read_paths
    write_paths(foray_dir, [
        PathInfo(id="a", description="test", priority=Priority.HIGH, hypothesis="h", status=PathStatus.RESOLVED),
    ])

    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir

    strategy = StrategyOutput(
        vision_assessment="ignore",
        decisions=[StrategyDecision(action="close", path_id="a", status=PathStatus.INCONCLUSIVE, reason="stale")],
    )
    orch._apply_strategy(strategy)

    paths = read_paths(foray_dir)
    assert paths[0].status == PathStatus.RESOLVED  # unchanged


def test_apply_strategy_no_decisions(tmp_path):
    """_apply_strategy with empty decisions is a no-op on paths."""
    from foray.models import StrategyOutput
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)

    from foray.state import write_paths, read_paths
    write_paths(foray_dir, [
        PathInfo(id="a", description="test", priority=Priority.HIGH, hypothesis="h"),
    ])

    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir

    strategy = StrategyOutput(vision_assessment="All good, stay the course")
    orch._apply_strategy(strategy)

    paths = read_paths(foray_dir)
    assert len(paths) == 1
    assert paths[0].status == PathStatus.OPEN


def test_apply_strategy_multiple_decisions(tmp_path):
    """_apply_strategy applies multiple decisions in sequence."""
    from foray.models import StrategyOutput, StrategyDecision
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)

    from foray.state import write_paths, read_paths
    write_paths(foray_dir, [
        PathInfo(id="a", description="test", priority=Priority.HIGH, hypothesis="h", status=PathStatus.OPEN),
        PathInfo(id="b", description="test2", priority=Priority.LOW, hypothesis="h2", status=PathStatus.OPEN),
    ])

    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir

    new_path = PathInfo(id="c", description="new", priority=Priority.HIGH, hypothesis="h3")
    strategy = StrategyOutput(
        vision_assessment="Pivoting",
        decisions=[
            StrategyDecision(action="close", path_id="a", status=PathStatus.INCONCLUSIVE, reason="stale"),
            StrategyDecision(action="reprioritize", path_id="b", priority=Priority.HIGH),
            StrategyDecision(action="open", new_path=new_path),
        ],
    )
    orch._apply_strategy(strategy)

    paths = read_paths(foray_dir)
    assert len(paths) == 3
    assert paths[0].status == PathStatus.INCONCLUSIVE
    assert paths[1].priority == Priority.HIGH
    assert paths[2].id == "c"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator.py -k "apply_strategy" -v`
Expected: AttributeError — `Orchestrator` has no `_apply_strategy` method.

- [ ] **Step 3: Write implementation**

In `src/foray/orchestrator.py`:

Add to imports at the top (around line 30):

```python
from foray.models import (
    ...
    StrategyOutput,
)
```

Add to state imports (around line 53):

```python
from foray.state import (
    ...
    read_strategy,
    write_strategy,
)
```

Add to context imports (around line 13):

```python
from foray.context import (
    ...
    build_strategist_context,
)
```

Add these methods to the `Orchestrator` class, after `_apply_experiment_result` (after line 702):

```python
    def _apply_strategy(self, strategy: StrategyOutput) -> None:
        """Apply strategist decisions to paths. Respects evaluator-resolved paths."""
        paths = read_paths(self.foray_dir)

        for decision in strategy.decisions:
            if decision.action == "close":
                for i, p in enumerate(paths):
                    if p.id == decision.path_id:
                        if p.status == PathStatus.RESOLVED:
                            logger.info(f"Strategist: skipping close of '{p.id}' — already resolved by evaluator")
                        else:
                            status = decision.status or PathStatus.INCONCLUSIVE
                            paths[i] = p.model_copy(update={"status": status})
                            _log(f"  Strategist: closed {p.id} ({status.value}) — {decision.reason}")
                        break

            elif decision.action == "open" and decision.new_path:
                new = decision.new_path.model_copy(update={"status": PathStatus.OPEN, "experiment_count": 0})
                paths.append(new)
                _log(f"  Strategist: opened {new.id} ({new.priority}) — {new.description[:60]}")

            elif decision.action == "reprioritize" and decision.priority:
                for i, p in enumerate(paths):
                    if p.id == decision.path_id:
                        paths[i] = p.model_copy(update={"priority": decision.priority})
                        _log(f"  Strategist: reprioritized {p.id} → {decision.priority}")
                        break

        write_paths(self.foray_dir, paths)

    def _run_strategist(self, round_num: int) -> None:
        """Dispatch strategist agent after a round completes."""
        state = read_run_state(self.foray_dir)

        # Skip conditions
        if round_num <= 1:
            return
        remaining = state.config.max_experiments - state.experiment_count
        if remaining <= 1:
            return
        paths = read_paths(self.foray_dir)
        if not any(p.status == PathStatus.OPEN for p in paths):
            return

        _log("  Strategist reviewing vision progress...", self._run_start)
        template = self._load_agent_prompt("strategist")

        previous = read_strategy(self.foray_dir)
        previous_assessment = previous.vision_assessment if previous else None

        ctx = build_strategist_context(self.foray_dir, state, previous_assessment)
        strategy_path = self.foray_dir / "state" / "strategy.json"

        result = dispatch(
            prompt=(
                f"{template}\n\n---\n\n{ctx}\n\n---\n\n"
                f"Write strategy JSON to: {strategy_path}"
            ),
            workdir=self.project_root,
            model=self.config.evaluator_model,
            max_turns=6,
            tools=["Read", "Write"],
            output_format="stream-json",
        )
        self._record_dispatch(result, f"round-{round_num}", "strategist")

        strategy = read_strategy(self.foray_dir)
        if strategy:
            if strategy.decisions:
                _log(f"  Strategist: {len(strategy.decisions)} decision(s)", self._run_start)
                self._apply_strategy(strategy)
            else:
                _log("  Strategist: stay the course", self._run_start)
        else:
            logger.warning(f"Strategist failed to produce output (exit={result.exit_code})")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator.py -k "apply_strategy" -v`
Expected: All 6 `_apply_strategy` tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foray/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: add _apply_strategy and _run_strategist to orchestrator"
```

---

### Task 7: Wire Strategist into the Main Loop

**Files:**
- Modify: `src/foray/orchestrator.py` (the `run()` method, around line 409)

- [ ] **Step 1: Write failing test**

Add to `tests/test_orchestrator.py`:

```python
def test_strategist_skips_round_1():
    """_run_strategist is a no-op on round 1."""
    from unittest.mock import patch
    config = RunConfig(vision_path="vision.md")
    orch = Orchestrator(Path("/tmp/fake"), config)
    orch.foray_dir = Path("/tmp/fake/.foray")
    orch._run_start = 0.0

    # Should not dispatch — if it tries, it'll crash on missing files
    orch._run_strategist(1)  # no error = skipped
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/test_orchestrator.py::test_strategist_skips_round_1 -v`
Expected: PASS (the skip condition `round_num <= 1` handles this).

- [ ] **Step 3: Add strategist call to run() loop**

In `src/foray/orchestrator.py`, in the `run()` method, after the round is written to `rounds.json` (after line 409 `logger.info(...)`) and before the `while True` loop continues, add:

```python
                # Phase 3: Strategic review
                self._run_strategist(round_num)
```

This goes right after `logger.info(f"Round {round_num} complete: {len(round_paths)} experiments")` on line 409.

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests PASS. Existing tests don't trigger round > 1, so the strategist is never dispatched in tests.

- [ ] **Step 5: Commit**

```bash
git add src/foray/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: wire strategist into main loop after each round"
```

---

### Task 8: Full Integration Verification

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests PASS, zero failures.

- [ ] **Step 2: Verify module map accuracy**

Run: `uv run python -c "from foray.models import StrategyDecision, StrategyOutput; print('models OK')"`
Run: `uv run python -c "from foray.state import write_strategy, read_strategy; print('state OK')"`
Run: `uv run python -c "from foray.context import build_strategist_context; print('context OK')"`

Expected: All print "OK".

- [ ] **Step 3: Verify prompt is installed**

Run: `ls src/foray/agents/strategist.md`
Expected: File exists.

- [ ] **Step 4: Run /simplify on changed code**

Run the simplify pass on all modified files.

- [ ] **Step 5: Run code-review agent on changed code**

Review all changes against the spec and CLAUDE.md principles.

- [ ] **Step 6: Final commit and push**

```bash
git add -A
git commit -m "feat: strategist agent — vision-level feedback loop between rounds"
git push
```
