# Evaluator Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three evaluator improvements — environment blocker detection with fast-fail (foray-bdl), single-experiment resolution override (foray-ejn), and hypothesis divergence flagging (foray-dj1).

**Architecture:** Add three new fields to `Evaluation` model, add two new guardrail rules to `apply_guardrails`, update evaluator/executor/planner prompt files. All changes are additive — no existing behavior changes unless the new fields are populated.

**Tech Stack:** Python 3.12+, Pydantic v2, pytest

---

### Task 1: Add new fields to Evaluation model

**Files:**
- Modify: `src/foray/models.py:102-115` (Evaluation class)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write failing tests for new Evaluation fields**

Add to `tests/test_models.py`:

```python
# --- Evaluation new fields (foray-bdl, foray-ejn, foray-dj1) ---


def test_evaluation_failure_type_default():
    """failure_type defaults to empty string when not provided."""
    ev = Evaluation(
        experiment_id="001", path_id="test", outcome="conclusive",
        path_status=PathStatus.OPEN, confidence=Confidence.HIGH, summary="Test",
    )
    assert ev.failure_type == ""


def test_evaluation_failure_type_null_coercion():
    """failure_type: null coerces to empty string."""
    raw_json = json.dumps({
        "experiment_id": "001", "path_id": "test", "outcome": "conclusive",
        "path_status": "open", "confidence": "high", "summary": "Test",
        "failure_type": None,
    })
    ev = Evaluation.model_validate_json(raw_json)
    assert ev.failure_type == ""


def test_evaluation_failure_type_roundtrip():
    """failure_type: 'environment' round-trips correctly."""
    ev = Evaluation(
        experiment_id="001", path_id="test", outcome="conclusive",
        path_status=PathStatus.BLOCKED, confidence=Confidence.LOW,
        summary="Missing creds", failure_type="environment",
    )
    restored = Evaluation.model_validate_json(ev.model_dump_json())
    assert restored.failure_type == "environment"


def test_evaluation_independent_verification_default():
    """independent_verification defaults to empty string."""
    ev = Evaluation(
        experiment_id="001", path_id="test", outcome="conclusive",
        path_status=PathStatus.RESOLVED, confidence=Confidence.HIGH, summary="Test",
    )
    assert ev.independent_verification == ""


def test_evaluation_independent_verification_null_coercion():
    """independent_verification: null coerces to empty string."""
    raw_json = json.dumps({
        "experiment_id": "001", "path_id": "test", "outcome": "conclusive",
        "path_status": "resolved", "confidence": "high", "summary": "Test",
        "independent_verification": None,
    })
    ev = Evaluation.model_validate_json(raw_json)
    assert ev.independent_verification == ""


def test_evaluation_hypothesis_alignment_default():
    """hypothesis_alignment defaults to empty string."""
    ev = Evaluation(
        experiment_id="001", path_id="test", outcome="conclusive",
        path_status=PathStatus.OPEN, confidence=Confidence.HIGH, summary="Test",
    )
    assert ev.hypothesis_alignment == ""


def test_evaluation_hypothesis_alignment_null_coercion():
    """hypothesis_alignment: null coerces to empty string."""
    raw_json = json.dumps({
        "experiment_id": "001", "path_id": "test", "outcome": "conclusive",
        "path_status": "open", "confidence": "high", "summary": "Test",
        "hypothesis_alignment": None,
    })
    ev = Evaluation.model_validate_json(raw_json)
    assert ev.hypothesis_alignment == ""


def test_evaluation_divergence_note_default():
    """divergence_note defaults to empty string."""
    ev = Evaluation(
        experiment_id="001", path_id="test", outcome="conclusive",
        path_status=PathStatus.OPEN, confidence=Confidence.HIGH, summary="Test",
    )
    assert ev.divergence_note == ""


def test_evaluation_divergence_note_null_coercion():
    """divergence_note: null coerces to empty string."""
    raw_json = json.dumps({
        "experiment_id": "001", "path_id": "test", "outcome": "conclusive",
        "path_status": "open", "confidence": "high", "summary": "Test",
        "divergence_note": None,
    })
    ev = Evaluation.model_validate_json(raw_json)
    assert ev.divergence_note == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -k "failure_type or independent_verification or hypothesis_alignment or divergence_note" -v`
Expected: FAIL — fields don't exist yet.

- [ ] **Step 3: Add the four fields to Evaluation**

In `src/foray/models.py`, add four fields to the `Evaluation` class after the existing `methodology` field (line 115):

```python
    failure_type: str = ""
    independent_verification: str = ""
    hypothesis_alignment: str = ""
    divergence_note: str = ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -k "failure_type or independent_verification or hypothesis_alignment or divergence_note" -v`
Expected: All 8 tests PASS.

- [ ] **Step 5: Run full model test suite**

Run: `uv run pytest tests/test_models.py -v`
Expected: All tests PASS (existing + new).

- [ ] **Step 6: Commit**

```bash
git add src/foray/models.py tests/test_models.py
git commit -m "feat(models): add evaluator improvement fields to Evaluation

Add failure_type, independent_verification, hypothesis_alignment,
and divergence_note fields for foray-bdl, foray-ejn, foray-dj1."
```

---

### Task 2: Add guardrail rules for single-experiment resolution and hypothesis divergence

**Files:**
- Modify: `src/foray/orchestrator.py:109-148` (apply_guardrails function)
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: Write failing tests for single-experiment resolution override (foray-ejn)**

Add to `tests/test_orchestrator.py`. First update the `_assessment` helper to accept new fields:

```python
def _assessment(
    path_id: str = "a",
    path_status: PathStatus = PathStatus.RESOLVED,
    confidence: Confidence = Confidence.HIGH,
    blocker: str = "",
    methodology: str = "",
    independent_verification: str = "",
    hypothesis_alignment: str = "",
    divergence_note: str = "",
    failure_type: str = "",
) -> Evaluation:
    return Evaluation(
        experiment_id="001", path_id=path_id, outcome="conclusive",
        path_status=path_status, confidence=confidence, summary="done",
        blocker_description=blocker, methodology=methodology,
        independent_verification=independent_verification,
        hypothesis_alignment=hypothesis_alignment,
        divergence_note=divergence_note,
        failure_type=failure_type,
    )
```

Then add the tests:

```python
# --- Single-experiment resolution override (foray-ejn) ---


def test_single_experiment_resolved_with_independent_verification():
    """1 experiment + independent methodology + verification evidence → RESOLVED."""
    findings = [_finding("001", "a")]
    a = _assessment(
        methodology="independent",
        independent_verification="trimesh exact-match comparison returned 0 delta",
    )
    assert apply_guardrails(a, _path(), findings) == PathStatus.RESOLVED


def test_single_experiment_rejected_without_verification_evidence():
    """1 experiment + independent methodology but empty verification → OPEN."""
    findings = [_finding("001", "a")]
    a = _assessment(methodology="independent", independent_verification="")
    assert apply_guardrails(a, _path(), findings) == PathStatus.OPEN


def test_single_experiment_rejected_with_self_evaluated():
    """1 experiment + self-evaluated methodology → OPEN (no override)."""
    findings = [_finding("001", "a")]
    a = _assessment(
        methodology="self-evaluated",
        independent_verification="I checked it myself",
        confidence=Confidence.MEDIUM,
    )
    assert apply_guardrails(a, _path(), findings) == PathStatus.OPEN


def test_single_experiment_rejected_low_confidence_even_with_verification():
    """1 experiment + independent + verification but LOW confidence → OPEN."""
    findings = [_finding("001", "a")]
    a = _assessment(
        confidence=Confidence.LOW,
        methodology="independent",
        independent_verification="test suite passed",
    )
    assert apply_guardrails(a, _path(), findings) == PathStatus.OPEN
```

- [ ] **Step 2: Write failing tests for hypothesis divergence guardrail (foray-dj1)**

Add to `tests/test_orchestrator.py`:

```python
# --- Hypothesis divergence guardrail (foray-dj1) ---


def test_diverged_hypothesis_blocks_resolution():
    """hypothesis_alignment='diverged' + RESOLVED → OPEN."""
    findings = [_finding("001", "a"), _finding("002", "a")]
    a = _assessment(hypothesis_alignment="diverged", divergence_note="Answered wrong question")
    assert apply_guardrails(a, _path(), findings) == PathStatus.OPEN


def test_diverged_hypothesis_falls_to_inconclusive_when_exhausted():
    """hypothesis_alignment='diverged' + RESOLVED + EXHAUSTED → INCONCLUSIVE."""
    findings = [
        _finding("001", "a"),
        _finding("002", "a"),
        _finding("003", "a", ExperimentStatus.EXHAUSTED),
    ]
    a = _assessment(hypothesis_alignment="diverged")
    assert apply_guardrails(a, _path(), findings, exp_status=ExperimentStatus.EXHAUSTED) == PathStatus.INCONCLUSIVE


def test_aligned_hypothesis_allows_resolution():
    """hypothesis_alignment='aligned' + RESOLVED + 2 experiments → RESOLVED."""
    findings = [_finding("001", "a"), _finding("002", "a")]
    a = _assessment(hypothesis_alignment="aligned")
    assert apply_guardrails(a, _path(), findings) == PathStatus.RESOLVED


def test_partial_alignment_allows_resolution():
    """hypothesis_alignment='partial' should not block resolution."""
    findings = [_finding("001", "a"), _finding("002", "a")]
    a = _assessment(hypothesis_alignment="partial")
    assert apply_guardrails(a, _path(), findings) == PathStatus.RESOLVED


def test_empty_alignment_allows_resolution():
    """Empty hypothesis_alignment (backwards compat) should not block resolution."""
    findings = [_finding("001", "a"), _finding("002", "a")]
    a = _assessment(hypothesis_alignment="")
    assert apply_guardrails(a, _path(), findings) == PathStatus.RESOLVED


def test_diverged_does_not_block_non_resolved_statuses():
    """hypothesis_alignment='diverged' only blocks RESOLVED, not OPEN/BLOCKED/INCONCLUSIVE."""
    a = _assessment(path_status=PathStatus.OPEN, hypothesis_alignment="diverged")
    assert apply_guardrails(a, _path(), []) == PathStatus.OPEN

    a = _assessment(
        path_status=PathStatus.BLOCKED, hypothesis_alignment="diverged",
        blocker="env issue",
    )
    assert apply_guardrails(a, _path(), []) == PathStatus.BLOCKED
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator.py -k "single_experiment or diverged or partial_alignment or empty_alignment or aligned_hypothesis" -v`
Expected: FAIL — new guardrail logic not implemented yet.

- [ ] **Step 4: Implement the guardrail changes**

Replace `apply_guardrails` in `src/foray/orchestrator.py`:

```python
def apply_guardrails(
    assessment: Evaluation,
    path: PathInfo,
    findings: list[Finding],
    exp_status: ExperimentStatus | None = None,
) -> PathStatus:
    """Apply deterministic guardrails to assessor's status recommendation.

    - Resolved requires 2+ non-failure experiments and at least medium confidence,
      UNLESS 1 experiment has independent methodology with cited verification.
    - Diverged hypothesis blocks resolution.
    - Blocked requires a non-empty blocker description.
    - When exp_status is EXHAUSTED, rejected recommendations fall to INCONCLUSIVE
      instead of OPEN to prevent infinite loops.
    """
    recommended = assessment.path_status
    fallback = (
        PathStatus.INCONCLUSIVE if exp_status == ExperimentStatus.EXHAUSTED
        else PathStatus.OPEN
    )

    if recommended == PathStatus.RESOLVED:
        # Diverged hypothesis blocks resolution regardless of experiment count
        if assessment.hypothesis_alignment == "diverged":
            logger.info(
                f"Guardrail: rejecting resolution of '{path.id}' "
                f"-- hypothesis diverged: {assessment.divergence_note}"
            )
            return fallback

        non_failures = sum(
            1 for f in findings
            if f.path_id == path.id
            and f.status in (ExperimentStatus.SUCCESS, ExperimentStatus.PARTIAL)
        )

        # Single-experiment override: independent methodology with cited verification
        has_independent_override = (
            non_failures == 1
            and assessment.methodology == "independent"
            and assessment.independent_verification
        )

        if non_failures < 2 and not has_independent_override:
            logger.info(
                f"Guardrail: rejecting resolution of '{path.id}' "
                f"-- only {non_failures} non-failure experiment(s)"
            )
            return fallback
        if assessment.confidence == Confidence.LOW:
            logger.info(f"Guardrail: rejecting resolution of '{path.id}' -- low confidence")
            return fallback

    if recommended == PathStatus.BLOCKED and not assessment.blocker_description:
        logger.info(f"Guardrail: rejecting block of '{path.id}' -- no blocker description")
        return fallback

    return recommended
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator.py -k "single_experiment or diverged or partial_alignment or empty_alignment or aligned_hypothesis" -v`
Expected: All 10 new tests PASS.

- [ ] **Step 6: Run full orchestrator test suite**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: All tests PASS (existing + new).

- [ ] **Step 7: Commit**

```bash
git add src/foray/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(guardrails): single-experiment resolution override and hypothesis divergence check

foray-ejn: Allow RESOLVED with 1 experiment when methodology is independent
and independent_verification is cited.
foray-dj1: Block RESOLVED when hypothesis_alignment is 'diverged'."
```

---

### Task 3: Update evaluator prompt

**Files:**
- Modify: `src/foray/agents/evaluator.md`

- [ ] **Step 1: Add `failure_type` to the JSON output spec**

In the JSON structure block (lines 7-24), add after `"methodology"`:

```json
    "failure_type": "environment|approach|<empty>",
    "independent_verification": "What independent tool/benchmark/test verified the result, if any",
    "hypothesis_alignment": "aligned|partial|diverged",
    "divergence_note": "If partial or diverged, explain how findings differ from the hypothesis"
```

- [ ] **Step 2: Update the Environment Failures section for pattern escalation**

Replace the "Environment Failures vs Approach Failures" section (lines 57-64) with:

```markdown
## Environment Failures vs Approach Failures

When an experiment FAILED due to a missing environment dependency (package not installed, credentials not available, tool not on PATH), the hypothesis was **not tested, not disproven**:
- Set `"failure_type": "environment"` in your assessment
- For approach failures (the hypothesis was tested and failed), set `"failure_type": "approach"`

**Single environment failure:** The path should remain **open** — there is no evidence against the hypothesis. Note the environment constraint in `planner_brief` so the next experiment can work around it.

**Repeated environment failures (2+ experiments hit the same constraint):** The environment cannot support this path. Recommend **blocked** with `blocker_description` explaining the infrastructure constraint. Do not keep the path open for retries that will hit the same wall.

Only count failures toward blocked/inconclusive when the failure reveals something about the hypothesis itself (e.g., the API returned an error that proves the approach won't work, not that credentials were missing).
```

- [ ] **Step 3: Add independent verification override section**

Add after the "Self-Evaluation Detection" section:

```markdown
## Independent Verification Override

When a **single experiment** produces a definitive answer verified by an independent tool, benchmark, or test suite (not self-evaluation):
- Set `"methodology": "independent"`
- Set `"independent_verification"` to a specific description of what verified the result (e.g., "trimesh exact-match comparison returned 0 delta", "pytest suite: 47/47 passed", "curl returned expected 200 with correct payload")
- You MAY recommend `"path_status": "resolved"` even with only one experiment

The verification must be **specific and cited** — "I verified it works" is not independent verification. Name the tool, the metric, and the result.
```

- [ ] **Step 4: Add hypothesis alignment section**

Add after the "Rules" section:

```markdown
## Hypothesis Alignment Check

Before recommending path status, compare the experiment's actual findings to the path's original hypothesis:

- **aligned** — the experiment directly tested the hypothesis and produced evidence for or against it
- **partial** — the experiment tested a related but narrower question (e.g., tested one sub-case of the hypothesis)
- **diverged** — the experiment answered a fundamentally different question than the hypothesis asked

Set `"hypothesis_alignment"` and, if partial or diverged, explain in `"divergence_note"`.

**Critical:** If alignment is "diverged", do NOT recommend "resolved". The path question has not been answered — it has been replaced with a different question. Recommend "open" so the next experiment can address the actual hypothesis.
```

- [ ] **Step 5: Commit**

```bash
git add src/foray/agents/evaluator.md
git commit -m "feat(evaluator): add environment escalation, verification override, and alignment check

foray-bdl: Evaluator now distinguishes env vs approach failures, escalates
to BLOCKED after repeated same-constraint failures.
foray-ejn: Evaluator can cite independent verification for single-experiment resolution.
foray-dj1: Evaluator must assess hypothesis alignment and flag divergence."
```

---

### Task 4: Update executor prompt for environment fast-fail

**Files:**
- Modify: `src/foray/agents/executor.md`

- [ ] **Step 1: Add Environment Constraint Fast-Fail section**

Add after the "Research Phase Gate" section (after line 33):

```markdown
### 3. Environment Constraint Fast-Fail

If a dependency check, credential check, or tool availability check fails during the Research Phase:
1. Write results immediately with status `FAILED`
2. In the Blocker section, name the **specific** missing dependency (e.g., "google-cloud-vision requires GOOGLE_APPLICATION_CREDENTIALS which is not set", not "credentials missing")
3. **Stop.** Do not attempt workarounds, alternative installs, or fallback approaches — that is the planner's job on the next experiment. Your job is to report the constraint clearly so the planner can route around it.

This applies to: missing Python packages that fail to install, missing system tools, missing credentials/API keys, missing data files referenced in the plan, and network endpoints that are unreachable.
```

Update the subsequent section numbers (existing "3. Time Budget Awareness" becomes "4.", etc.).

- [ ] **Step 2: Commit**

```bash
git add src/foray/agents/executor.md
git commit -m "feat(executor): add environment constraint fast-fail rule

foray-bdl: Executor must fail immediately on environment constraints
instead of spending budget on workarounds."
```

---

### Task 5: Update planner prompt for environment constraint awareness

**Files:**
- Modify: `src/foray/agents/planner.md`

- [ ] **Step 1: Strengthen the environment failure awareness section**

Replace the "Failure Awareness" section (lines 101-111) with:

```markdown
## Failure Awareness

If previous experiments failed:
- **PARTIAL:** Build on the partial work
- **FAILED:** Diagnose the blocker. Plan a DIFFERENT approach
- **FAILED (environment):** A missing dependency or credential prevented testing. Check the Environment section and either design around the constraint or choose a different approach that uses available tools. **If all viable approaches require the blocked dependency, signal EXHAUSTED** — do not plan another experiment that will hit the same wall.
- **INFEASIBLE (capability):** Rescope to available executor capabilities
- **INFEASIBLE (research):** Feasibility research showed the hypothesis is not viable. Change the hypothesis or abandon the path
- **CRASH:** Simplify the experiment scope

**Never plan an experiment that hits the same blocker as a previous failure.** If previous experiments report the same environment constraint (e.g., two experiments both failed because `opencv-python` could not be installed), treat that constraint as permanent for this run. Do not plan a third experiment requiring the same dependency.
```

- [ ] **Step 2: Commit**

```bash
git add src/foray/agents/planner.md
git commit -m "feat(planner): treat repeated environment constraints as permanent blockers

foray-bdl: Planner must not plan experiments requiring a dependency that
has failed to install in 2+ prior experiments. Signal EXHAUSTED instead."
```

---

### Task 6: Full test suite and final verification

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests PASS across all modules.

- [ ] **Step 2: Run /simplify on changed code**

Review `models.py` and `orchestrator.py` for any unnecessary complexity in the new code.

- [ ] **Step 3: Run code-review agent on changed code**

Review all changed files for correctness, style consistency, and adherence to project conventions.

- [ ] **Step 4: Final commit if simplify/review produced changes**

```bash
git add -A
git commit -m "refactor: simplify evaluator improvement code per review"
```

- [ ] **Step 5: Close beads issues**

```bash
bd close foray-bdl foray-ejn foray-dj1
```

- [ ] **Step 6: Push**

```bash
git push
```
