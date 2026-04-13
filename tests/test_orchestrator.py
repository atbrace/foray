import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from foray.models import (
    Confidence,
    DispatchResult,
    Evaluation,
    ExperimentResult,
    ExperimentStatus,
    Finding,
    PathInfo,
    PathStatus,
    Priority,
    RunConfig,
    RunState,
    TimingRecord,
)
from foray.orchestrator import Orchestrator, _format_seconds, apply_guardrails
from foray.state import append_timing, init_directory, read_timing


def _path(id: str = "a") -> PathInfo:
    return PathInfo(id=id, description="test", priority=Priority.HIGH, hypothesis="test")


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


def _finding(
    exp_id: str, path_id: str, status: ExperimentStatus = ExperimentStatus.SUCCESS,
) -> Finding:
    return Finding(experiment_id=exp_id, path_id=path_id, status=status, summary="ok", one_liner="ok")


# --- Resolved requires depth ---


def test_resolved_rejected_with_one_experiment():
    findings = [_finding("001", "a")]
    assert apply_guardrails(_assessment(), _path(), findings) == PathStatus.OPEN


def test_resolved_accepted_with_two_experiments():
    findings = [_finding("001", "a"), _finding("002", "a")]
    assert apply_guardrails(_assessment(), _path(), findings) == PathStatus.RESOLVED


def test_resolved_only_counts_non_failures():
    findings = [
        _finding("001", "a"),
        _finding("002", "a", ExperimentStatus.FAILED),
    ]
    assert apply_guardrails(_assessment(), _path(), findings) == PathStatus.OPEN


# --- Low confidence blocks resolution ---


def test_low_confidence_blocks_resolution():
    findings = [_finding("001", "a"), _finding("002", "a")]
    assert apply_guardrails(_assessment(confidence=Confidence.LOW), _path(), findings) == PathStatus.OPEN


def test_medium_confidence_allows_resolution():
    findings = [_finding("001", "a"), _finding("002", "a")]
    assert apply_guardrails(_assessment(confidence=Confidence.MEDIUM), _path(), findings) == PathStatus.RESOLVED


# --- Blocked requires evidence ---


def test_blocked_rejected_without_description():
    assert apply_guardrails(_assessment(path_status=PathStatus.BLOCKED, blocker=""), _path(), []) == PathStatus.OPEN


def test_blocked_accepted_with_description():
    a = _assessment(path_status=PathStatus.BLOCKED, blocker="Cannot install dependency")
    assert apply_guardrails(a, _path(), []) == PathStatus.BLOCKED


# --- Pass-through ---


def test_open_passes_through():
    assert apply_guardrails(_assessment(path_status=PathStatus.OPEN), _path(), []) == PathStatus.OPEN


def test_inconclusive_passes_through():
    assert apply_guardrails(_assessment(path_status=PathStatus.INCONCLUSIVE), _path(), []) == PathStatus.INCONCLUSIVE


# --- Exhaustion scenarios ---


def test_exhausted_path_can_be_resolved():
    """After exhaustion, if evaluator says resolved and path has 2+ successes, it resolves."""
    findings = [
        _finding("001", "a"),
        _finding("002", "a"),
        _finding("003", "a", ExperimentStatus.EXHAUSTED),
    ]
    assert apply_guardrails(_assessment(path_status=PathStatus.RESOLVED), _path(), findings) == PathStatus.RESOLVED


def test_exhausted_path_can_be_inconclusive():
    """After exhaustion, evaluator can mark path inconclusive."""
    findings = [
        _finding("001", "a"),
        _finding("002", "a", ExperimentStatus.EXHAUSTED),
    ]
    assert apply_guardrails(
        _assessment(path_status=PathStatus.INCONCLUSIVE), _path(), findings
    ) == PathStatus.INCONCLUSIVE


def test_exhausted_rejected_resolved_falls_to_inconclusive():
    """Bug fix: EXHAUSTED + guardrails rejecting RESOLVED should be INCONCLUSIVE, not OPEN."""
    findings = [
        _finding("001", "a", ExperimentStatus.FAILED),
        _finding("002", "a", ExperimentStatus.EXHAUSTED),
    ]
    assert apply_guardrails(
        _assessment(path_status=PathStatus.RESOLVED), _path(), findings,
        exp_status=ExperimentStatus.EXHAUSTED,
    ) == PathStatus.INCONCLUSIVE


def test_exhausted_rejected_resolved_low_confidence_falls_to_inconclusive():
    """EXHAUSTED + low confidence rejection should also be INCONCLUSIVE."""
    findings = [
        _finding("001", "a"),
        _finding("002", "a"),
        _finding("003", "a", ExperimentStatus.EXHAUSTED),
    ]
    assert apply_guardrails(
        _assessment(path_status=PathStatus.RESOLVED, confidence=Confidence.LOW), _path(), findings,
        exp_status=ExperimentStatus.EXHAUSTED,
    ) == PathStatus.INCONCLUSIVE


def test_non_exhausted_rejected_resolved_stays_open():
    """Without EXHAUSTED, rejected RESOLVED should still return OPEN (existing behavior)."""
    findings = [_finding("001", "a")]
    assert apply_guardrails(
        _assessment(path_status=PathStatus.RESOLVED), _path(), findings,
        exp_status=ExperimentStatus.SUCCESS,
    ) == PathStatus.OPEN


# --- Single-experiment resolution override (foray-ejn) ---


def test_single_experiment_resolved_with_independent_verification():
    """1 experiment + independent methodology + verification evidence -> RESOLVED."""
    findings = [_finding("001", "a")]
    a = _assessment(
        methodology="independent",
        independent_verification="trimesh exact-match comparison returned 0 delta",
    )
    assert apply_guardrails(a, _path(), findings) == PathStatus.RESOLVED


def test_single_experiment_rejected_without_verification_evidence():
    """1 experiment + independent methodology but empty verification -> OPEN."""
    findings = [_finding("001", "a")]
    a = _assessment(methodology="independent", independent_verification="")
    assert apply_guardrails(a, _path(), findings) == PathStatus.OPEN


def test_single_experiment_rejected_with_self_evaluated():
    """1 experiment + self-evaluated methodology -> OPEN (no override)."""
    findings = [_finding("001", "a")]
    a = _assessment(
        methodology="self-evaluated",
        independent_verification="I checked it myself",
        confidence=Confidence.MEDIUM,
    )
    assert apply_guardrails(a, _path(), findings) == PathStatus.OPEN


def test_single_experiment_rejected_low_confidence_even_with_verification():
    """1 experiment + independent + verification but LOW confidence -> OPEN."""
    findings = [_finding("001", "a")]
    a = _assessment(
        confidence=Confidence.LOW,
        methodology="independent",
        independent_verification="test suite passed",
    )
    assert apply_guardrails(a, _path(), findings) == PathStatus.OPEN


# --- Hypothesis divergence guardrail (foray-dj1) ---


def test_diverged_hypothesis_blocks_resolution():
    """hypothesis_alignment='diverged' + RESOLVED -> OPEN."""
    findings = [_finding("001", "a"), _finding("002", "a")]
    a = _assessment(hypothesis_alignment="diverged", divergence_note="Answered wrong question")
    assert apply_guardrails(a, _path(), findings) == PathStatus.OPEN


def test_diverged_hypothesis_falls_to_inconclusive_when_exhausted():
    """hypothesis_alignment='diverged' + RESOLVED + EXHAUSTED -> INCONCLUSIVE."""
    findings = [
        _finding("001", "a"),
        _finding("002", "a"),
        _finding("003", "a", ExperimentStatus.EXHAUSTED),
    ]
    a = _assessment(hypothesis_alignment="diverged")
    assert apply_guardrails(a, _path(), findings, exp_status=ExperimentStatus.EXHAUSTED) == PathStatus.INCONCLUSIVE


def test_aligned_hypothesis_allows_resolution():
    """hypothesis_alignment='aligned' + RESOLVED + 2 experiments -> RESOLVED."""
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


# --- Environment failure escalation guardrail (foray-bdl enforcement) ---


def test_env_failures_escalate_to_inconclusive():
    """2+ FAILED experiments on same path + evaluator says OPEN → INCONCLUSIVE."""
    findings = [
        _finding("001", "a", ExperimentStatus.FAILED),
        _finding("002", "a", ExperimentStatus.FAILED),
    ]
    a = _assessment(path_status=PathStatus.OPEN)
    assert apply_guardrails(a, _path(), findings) == PathStatus.INCONCLUSIVE


def test_single_env_failure_stays_open():
    """1 FAILED experiment + evaluator says OPEN → OPEN (give it another shot)."""
    findings = [_finding("001", "a", ExperimentStatus.FAILED)]
    a = _assessment(path_status=PathStatus.OPEN)
    assert apply_guardrails(a, _path(), findings) == PathStatus.OPEN


def test_env_failures_only_count_same_path():
    """FAILED experiments on other paths don't count toward escalation."""
    findings = [
        _finding("001", "a", ExperimentStatus.FAILED),
        _finding("002", "b", ExperimentStatus.FAILED),
    ]
    a = _assessment(path_status=PathStatus.OPEN)
    assert apply_guardrails(a, _path(), findings) == PathStatus.OPEN


def test_env_failures_dont_override_blocked():
    """If evaluator already recommends BLOCKED with description, don't interfere."""
    findings = [
        _finding("001", "a", ExperimentStatus.FAILED),
        _finding("002", "a", ExperimentStatus.FAILED),
    ]
    a = _assessment(path_status=PathStatus.BLOCKED, blocker="Missing opencv")
    assert apply_guardrails(a, _path(), findings) == PathStatus.BLOCKED


def test_env_failures_dont_override_resolved():
    """Environment failures don't prevent resolution if evaluator says resolved."""
    findings = [
        _finding("001", "a", ExperimentStatus.FAILED),
        _finding("002", "a", ExperimentStatus.FAILED),
        _finding("003", "a", ExperimentStatus.SUCCESS),
        _finding("004", "a", ExperimentStatus.SUCCESS),
    ]
    a = _assessment(path_status=PathStatus.RESOLVED)
    assert apply_guardrails(a, _path(), findings) == PathStatus.RESOLVED


# --- Evaluator failure diagnostics (GH-18) ---


def _setup_eval_test(tmp_path, evaluator_dispatch_result, capture_prompts=None):
    """Set up an orchestrator that runs through plan → execute → evaluate.

    Returns (orch, state, path) ready for `orch._run_experiment(path, "001", [], state)`.
    `evaluator_dispatch_result` controls what the evaluator dispatch returns.
    If `capture_prompts` is a list, evaluator prompts are appended to it.
    """
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0
    orch._prompt_cache = {"planner": "p", "executor": "e", "evaluator": "ev"}

    path = PathInfo(id="a", description="test", priority=Priority.HIGH, hypothesis="test")
    plan_path = foray_dir / "experiments" / "001_plan.md"
    results_path = foray_dir / "experiments" / "001_results.md"

    def dispatch_side_effect(prompt: str, **kwargs):
        if "ev" in prompt and "_eval.json" in prompt:
            if capture_prompts is not None:
                capture_prompts.append(prompt)
            return evaluator_dispatch_result
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text("# Plan\nTest plan")
        return MagicMock(exit_code=0, stdout="", stderr="", elapsed_seconds=1.0)

    def write_results(*args, **kwargs):
        results_path.write_text("## Status\nSUCCESS\n\n## Findings\nTest")
        return MagicMock(exit_code=0, stdout="", stderr="", elapsed_seconds=5.0)

    return orch, state, path, dispatch_side_effect, write_results


@patch("foray.orchestrator.dispatch")
@patch("foray.orchestrator.dispatch_executor")
@patch("foray.orchestrator.create_worktree")
@patch("foray.orchestrator.cleanup_worktree")
@patch("foray.orchestrator.copy_artifacts")
@patch("foray.orchestrator.enforce_worktree_limit")
@patch("foray.orchestrator.read_evaluation")
def test_evaluator_failure_logs_diagnostics(
    mock_eval, mock_enforce, mock_copy, mock_cleanup, mock_create_wt,
    mock_dispatch_exec, mock_dispatch, tmp_path, caplog,
):
    """When evaluator produces no assessment file, warning is logged with exit code and stderr."""
    eval_result = MagicMock(exit_code=1, stdout="", stderr="model overloaded error", elapsed_seconds=2.0)
    orch, state, path, dispatch_se, write_results = _setup_eval_test(tmp_path, eval_result)
    mock_dispatch.side_effect = dispatch_se
    mock_dispatch_exec.side_effect = write_results
    mock_create_wt.return_value = tmp_path / "worktree"
    (tmp_path / "worktree").mkdir()
    mock_eval.return_value = None

    with caplog.at_level(logging.WARNING, logger="foray.orchestrator"):
        result = orch._run_experiment(path, "001", [], state)

    assert result.finding.summary == "(assessment failed)"
    assert result.assessment is None
    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("Evaluator produced no assessment for 001" in m for m in warning_msgs)
    assert any("exit=1" in m for m in warning_msgs)
    assert any("model overloaded error" in m for m in warning_msgs)


@patch("foray.orchestrator.dispatch")
@patch("foray.orchestrator.dispatch_executor")
@patch("foray.orchestrator.create_worktree")
@patch("foray.orchestrator.cleanup_worktree")
@patch("foray.orchestrator.copy_artifacts")
@patch("foray.orchestrator.enforce_worktree_limit")
@patch("foray.orchestrator.read_evaluation")
def test_evaluator_prompt_includes_experiment_id(
    mock_eval, mock_enforce, mock_copy, mock_cleanup, mock_create_wt,
    mock_dispatch_exec, mock_dispatch, tmp_path,
):
    """Normal evaluator dispatch should pass experiment_id explicitly in the prompt."""
    prompts = []
    eval_result = MagicMock(exit_code=0, stdout="", stderr="", elapsed_seconds=2.0)
    orch, state, path, dispatch_se, write_results = _setup_eval_test(tmp_path, eval_result, capture_prompts=prompts)
    mock_dispatch.side_effect = dispatch_se
    mock_dispatch_exec.side_effect = write_results
    mock_create_wt.return_value = tmp_path / "worktree"
    (tmp_path / "worktree").mkdir()
    mock_eval.return_value = None

    orch._run_experiment(path, "001", [], state)

    assert len(prompts) == 1
    assert "Use experiment_id: 001" in prompts[0]


@patch("foray.orchestrator.dispatch")
@patch("foray.orchestrator.dispatch_executor")
@patch("foray.orchestrator.create_worktree")
@patch("foray.orchestrator.cleanup_worktree")
@patch("foray.orchestrator.copy_artifacts")
@patch("foray.orchestrator.enforce_worktree_limit")
@patch("foray.orchestrator.read_evaluation")
def test_evaluator_failure_logs_to_console(
    mock_eval, mock_enforce, mock_copy, mock_cleanup, mock_create_wt,
    mock_dispatch_exec, mock_dispatch, tmp_path, capsys,
):
    """When evaluator fails, diagnostic output should be visible in console via _log."""
    eval_result = MagicMock(exit_code=1, stdout="some stdout", stderr="some error", elapsed_seconds=2.0)
    orch, state, path, dispatch_se, write_results = _setup_eval_test(tmp_path, eval_result)
    mock_dispatch.side_effect = dispatch_se
    mock_dispatch_exec.side_effect = write_results
    mock_create_wt.return_value = tmp_path / "worktree"
    (tmp_path / "worktree").mkdir()
    mock_eval.return_value = None

    orch._run_experiment(path, "001", [], state)

    captured = capsys.readouterr()
    assert "assessment failed" in captured.out.lower() or "no assessment" in captured.out.lower()


# --- Synthesis on early termination (GH-17) ---


@patch("foray.orchestrator.Orchestrator._run_synthesis")
@patch("foray.orchestrator.read_findings")
@patch("foray.orchestrator.read_paths")
@patch("foray.orchestrator.read_run_state")
@patch("foray.orchestrator.check_consecutive_failures")
def test_synthesis_runs_after_circuit_breaker(
    mock_circuit, mock_read_state, mock_read_paths, mock_read_findings, mock_synth, tmp_path,
):
    """Synthesis must run even when circuit breaker fires."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config, experiment_count=5)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0

    mock_read_state.return_value = state
    mock_read_paths.return_value = [_path()]
    mock_read_findings.return_value = [_finding("001", "a")]
    mock_circuit.return_value = True  # circuit breaker fires

    orch.run()
    mock_synth.assert_called_once()


@patch("foray.orchestrator.Orchestrator._run_synthesis")
@patch("foray.orchestrator.Orchestrator._apply_experiment_result")
@patch("foray.orchestrator.Orchestrator._run_experiment")
@patch("foray.orchestrator.read_findings")
@patch("foray.orchestrator.read_paths")
@patch("foray.orchestrator.read_run_state")
@patch("foray.orchestrator.read_rounds")
@patch("foray.orchestrator.write_run_state")
@patch("foray.orchestrator.write_rounds")
@patch("foray.orchestrator.should_continue")
@patch("foray.orchestrator.check_consecutive_failures")
@patch("foray.orchestrator.get_round_paths")
def test_synthesis_runs_after_exception_in_merge(
    mock_get_round, mock_circuit, mock_should_continue,
    mock_write_rounds, mock_write_state, mock_read_rounds,
    mock_read_state, mock_read_paths, mock_read_findings,
    mock_run_exp, mock_apply, mock_synth, tmp_path,
):
    """Synthesis must run even when an exception occurs during merge phase."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config, experiment_count=0)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0
    orch._prompt_cache = {"planner": "p", "executor": "e", "evaluator": "ev"}

    mock_read_state.return_value = state
    mock_read_paths.return_value = [_path()]
    mock_read_findings.return_value = []
    mock_should_continue.return_value = True
    mock_circuit.return_value = False
    mock_get_round.return_value = ["a"]
    mock_read_rounds.return_value = []

    result = ExperimentResult(
        experiment_id="001", path_id="a", exp_status=ExperimentStatus.SUCCESS,
        finding=_finding("001", "a"),
    )
    mock_run_exp.return_value = result
    mock_apply.side_effect = RuntimeError("State corruption")

    # The exception should propagate, but synthesis must still run
    try:
        orch.run()
    except RuntimeError:
        pass
    mock_synth.assert_called_once()


# --- Synthesis retry and failure ---


@patch("foray.orchestrator.dispatch")
@patch("foray.orchestrator.build_synthesizer_context", return_value="ctx")
def test_synthesis_retries_on_failure(mock_ctx, mock_dispatch, tmp_path):
    """Synthesis retries once when first dispatch fails to create synthesis.md."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0
    orch._prompt_cache = {"synthesizer": "synth prompt"}

    synthesis_path = foray_dir / "synthesis.md"

    fail_result = DispatchResult(exit_code=1, stdout="", stderr="agent crashed", elapsed_seconds=5.0)
    ok_result = DispatchResult(exit_code=0, stdout="", stderr="", elapsed_seconds=10.0)

    call_count = 0

    def side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return fail_result
        # Second call: create the file and succeed
        synthesis_path.write_text("# Report")
        return ok_result

    mock_dispatch.side_effect = side_effect

    orch._run_synthesis()

    assert mock_dispatch.call_count == 2
    assert synthesis_path.exists()


@patch("foray.orchestrator.dispatch")
@patch("foray.orchestrator.build_synthesizer_context", return_value="ctx")
def test_synthesis_logs_warning_on_double_failure(mock_ctx, mock_dispatch, tmp_path, caplog):
    """Both synthesis attempts fail — warning is logged, no exception raised."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0
    orch._prompt_cache = {"synthesizer": "synth prompt"}

    fail_result = DispatchResult(exit_code=1, stdout="", stderr="agent crashed", elapsed_seconds=5.0)
    mock_dispatch.return_value = fail_result

    with caplog.at_level(logging.WARNING, logger="foray.orchestrator"):
        orch._run_synthesis()  # Should not raise

    assert mock_dispatch.call_count == 2
    assert not (foray_dir / "synthesis.md").exists()
    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("Synthesis failed after 2 attempts" in m for m in warning_msgs)


# --- Dispatch elapsed time logging (GH-8) ---


# --- Exhausted path relaxed resolution (foray-82e) ---


def test_exhausted_resolves_with_one_non_failure():
    """EXHAUSTED + evaluator says RESOLVED + 1 non-failure experiment -> RESOLVED."""
    findings = [
        _finding("001", "a"),
        _finding("002", "a", ExperimentStatus.EXHAUSTED),
    ]
    assert apply_guardrails(
        _assessment(path_status=PathStatus.RESOLVED),
        _path(), findings,
        exp_status=ExperimentStatus.EXHAUSTED,
    ) == PathStatus.RESOLVED


def test_exhausted_still_requires_at_least_one_non_failure():
    """EXHAUSTED + zero non-failure experiments -> INCONCLUSIVE."""
    findings = [
        _finding("001", "a", ExperimentStatus.FAILED),
        _finding("002", "a", ExperimentStatus.EXHAUSTED),
    ]
    assert apply_guardrails(
        _assessment(path_status=PathStatus.RESOLVED),
        _path(), findings,
        exp_status=ExperimentStatus.EXHAUSTED,
    ) == PathStatus.INCONCLUSIVE


def test_exhausted_still_blocks_low_confidence():
    """EXHAUSTED + LOW confidence -> INCONCLUSIVE even with non-failure experiments."""
    findings = [
        _finding("001", "a"),
        _finding("002", "a", ExperimentStatus.EXHAUSTED),
    ]
    assert apply_guardrails(
        _assessment(path_status=PathStatus.RESOLVED, confidence=Confidence.LOW),
        _path(), findings,
        exp_status=ExperimentStatus.EXHAUSTED,
    ) == PathStatus.INCONCLUSIVE


def test_non_exhausted_still_requires_two():
    """Without EXHAUSTED, 1 non-failure + RESOLVED -> OPEN (unchanged behavior)."""
    findings = [_finding("001", "a")]
    assert apply_guardrails(
        _assessment(path_status=PathStatus.RESOLVED),
        _path(), findings,
    ) == PathStatus.OPEN


# --- Token tracking for all agent types (foray-82m) ---


def test_planner_and_evaluator_track_tokens(tmp_path):
    """Planner and evaluator dispatches extract tokens via stream-json and persist them."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0
    orch._prompt_cache = {"planner": "p", "executor": "e", "evaluator": "ev"}

    path = PathInfo(id="a", description="test", priority=Priority.HIGH, hypothesis="test")
    plan_path = foray_dir / "experiments" / "001_plan.md"
    results_path = foray_dir / "experiments" / "001_results.md"

    planner_stdout = '{"type":"result","usage":{"input_tokens":1000,"output_tokens":200},"total_cost_usd":0.05}\n'
    eval_stdout = '{"type":"result","usage":{"input_tokens":500,"output_tokens":100},"total_cost_usd":0.02}\n'

    def dispatch_side_effect(prompt, **kwargs):
        if "_eval.json" in prompt:
            return DispatchResult(exit_code=0, stdout=eval_stdout, stderr="", elapsed_seconds=9.0)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text("# Plan\nTest plan")
        return DispatchResult(exit_code=0, stdout=planner_stdout, stderr="", elapsed_seconds=3.0)

    def write_results(*args, **kwargs):
        results_path.write_text("## Status\nSUCCESS\n\n## Findings\nTest")
        exec_stdout = '{"type":"result","usage":{"input_tokens":5000,"output_tokens":1000},"total_cost_usd":0.20}\n'
        return DispatchResult(exit_code=0, stdout=exec_stdout, stderr="", elapsed_seconds=45.0)

    with patch("foray.orchestrator.dispatch", side_effect=dispatch_side_effect), \
         patch("foray.orchestrator.dispatch_executor", side_effect=write_results), \
         patch("foray.orchestrator.create_worktree", return_value=tmp_path / "worktree"), \
         patch("foray.orchestrator.cleanup_worktree"), \
         patch("foray.orchestrator.copy_artifacts"), \
         patch("foray.orchestrator.enforce_worktree_limit"), \
         patch("foray.orchestrator.read_evaluation") as mock_eval:
        (tmp_path / "worktree").mkdir(exist_ok=True)
        mock_eval.return_value = Evaluation(
            experiment_id="001", path_id="a", outcome="conclusive",
            path_status=PathStatus.OPEN, confidence=Confidence.HIGH, summary="done",
        )
        orch._run_experiment(path, "001", [], state)

    records = read_timing(foray_dir)
    planner_recs = [r for r in records if r.agent_type == "planner"]
    eval_recs = [r for r in records if r.agent_type == "evaluator"]
    exec_recs = [r for r in records if r.agent_type == "executor"]

    assert len(planner_recs) == 1
    assert planner_recs[0].input_tokens == 1000
    assert planner_recs[0].output_tokens == 200
    assert planner_recs[0].cost_usd == 0.05

    assert len(eval_recs) == 1
    assert eval_recs[0].input_tokens == 500
    assert eval_recs[0].output_tokens == 100
    assert eval_recs[0].cost_usd == 0.02

    assert len(exec_recs) == 1
    assert exec_recs[0].input_tokens == 5000
    assert exec_recs[0].output_tokens == 1000


def test_all_dispatches_use_stream_json(tmp_path):
    """All dispatch calls pass output_format='stream-json' for token extraction."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0
    orch._prompt_cache = {"planner": "p", "executor": "e", "evaluator": "ev"}

    path = PathInfo(id="a", description="test", priority=Priority.HIGH, hypothesis="test")
    plan_path = foray_dir / "experiments" / "001_plan.md"
    results_path = foray_dir / "experiments" / "001_results.md"

    dispatch_kwargs_log = []

    def dispatch_side_effect(prompt, **kwargs):
        dispatch_kwargs_log.append(kwargs)
        if "_eval.json" in prompt:
            return DispatchResult(exit_code=0, stdout="", stderr="", elapsed_seconds=2.0)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text("# Plan\nTest plan")
        return DispatchResult(exit_code=0, stdout="", stderr="", elapsed_seconds=1.0)

    def write_results(*args, **kwargs):
        results_path.write_text("## Status\nSUCCESS\n\n## Findings\nTest")
        return DispatchResult(exit_code=0, stdout="", stderr="", elapsed_seconds=5.0)

    with patch("foray.orchestrator.dispatch", side_effect=dispatch_side_effect), \
         patch("foray.orchestrator.dispatch_executor", side_effect=write_results), \
         patch("foray.orchestrator.create_worktree", return_value=tmp_path / "worktree"), \
         patch("foray.orchestrator.cleanup_worktree"), \
         patch("foray.orchestrator.copy_artifacts"), \
         patch("foray.orchestrator.enforce_worktree_limit"), \
         patch("foray.orchestrator.read_evaluation") as mock_eval:
        (tmp_path / "worktree").mkdir(exist_ok=True)
        mock_eval.return_value = Evaluation(
            experiment_id="001", path_id="a", outcome="conclusive",
            path_status=PathStatus.OPEN, confidence=Confidence.HIGH, summary="done",
        )
        orch._run_experiment(path, "001", [], state)

    # Both planner and evaluator dispatch calls should use stream-json
    for i, kwargs in enumerate(dispatch_kwargs_log):
        assert kwargs.get("output_format") == "stream-json", (
            f"dispatch call {i} missing output_format='stream-json': {kwargs}"
        )


def test_timing_stats_show_per_agent_tokens(tmp_path):
    """_format_timing_stats includes per-agent token counts when available."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir

    append_timing(foray_dir, TimingRecord(
        experiment_id="001", agent_type="planner", elapsed_seconds=3.0,
        input_tokens=1000, output_tokens=200, cost_usd=0.05,
    ))
    append_timing(foray_dir, TimingRecord(
        experiment_id="001", agent_type="executor", elapsed_seconds=45.0,
        input_tokens=5000, output_tokens=1000, cost_usd=0.20,
    ))
    append_timing(foray_dir, TimingRecord(
        experiment_id="001", agent_type="evaluator", elapsed_seconds=9.0,
        input_tokens=500, output_tokens=100, cost_usd=0.02,
    ))

    stats = orch._format_timing_stats()
    # Per-agent token info should appear
    assert "1,000 in" in stats and "200 out" in stats   # planner
    assert "5,000 in" in stats and "1,000 out" in stats  # executor
    assert "500 in" in stats and "100 out" in stats      # evaluator


def test_format_seconds_under_minute():
    assert _format_seconds(3.0) == "3s"
    assert _format_seconds(59.4) == "59s"


def test_format_seconds_boundary():
    assert _format_seconds(60.0) == "1.0m"


def test_format_seconds_minutes():
    assert _format_seconds(90.0) == "1.5m"
    assert _format_seconds(300.0) == "5.0m"


def test_format_seconds_hours():
    assert _format_seconds(3600.0) == "1.0h"
    assert _format_seconds(7200.0) == "2.0h"


@patch("foray.orchestrator.dispatch")
@patch("foray.orchestrator.dispatch_executor")
@patch("foray.orchestrator.create_worktree")
@patch("foray.orchestrator.cleanup_worktree")
@patch("foray.orchestrator.copy_artifacts")
@patch("foray.orchestrator.enforce_worktree_limit")
@patch("foray.orchestrator.read_evaluation")
def test_experiment_logs_dispatch_elapsed(
    mock_eval, mock_enforce, mock_copy, mock_cleanup, mock_create_wt,
    mock_dispatch_exec, mock_dispatch, tmp_path, capsys,
):
    """Each agent dispatch logs its elapsed time in progress output (GH-8)."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0
    orch._prompt_cache = {"planner": "p", "executor": "e", "evaluator": "ev"}

    path = PathInfo(
        id="a", description="test", priority=Priority.HIGH, hypothesis="test",
    )

    plan_path = foray_dir / "experiments" / "001_plan.md"

    def dispatch_side_effect(prompt, **kwargs):
        if "_eval.json" in prompt:
            return MagicMock(exit_code=0, stdout="", stderr="", elapsed_seconds=9.0)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text("# Plan\nTest plan")
        return MagicMock(exit_code=0, stdout="", stderr="", elapsed_seconds=3.0)

    mock_dispatch.side_effect = dispatch_side_effect

    results_path = foray_dir / "experiments" / "001_results.md"

    def write_results(*args, **kwargs):
        results_path.write_text("## Status\nSUCCESS\n\n## Findings\nTest")
        return MagicMock(exit_code=0, stdout="", stderr="", elapsed_seconds=45.0)

    mock_dispatch_exec.side_effect = write_results
    mock_create_wt.return_value = tmp_path / "worktree"
    (tmp_path / "worktree").mkdir()
    mock_eval.return_value = Evaluation(
        experiment_id="001", path_id="a", outcome="conclusive",
        path_status=PathStatus.OPEN, confidence=Confidence.HIGH, summary="done",
    )

    orch._run_experiment(path, "001", [], state)

    captured = capsys.readouterr()
    assert "planned (3s)" in captured.out
    assert "executed (45s)" in captured.out
    assert "evaluated (9s)" in captured.out


def test_timing_accumulation(tmp_path):
    """Orchestrator accumulates per-agent-type dispatch timing (GH-8)."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir

    orch._persist_timing(TimingRecord(experiment_id="001", agent_type="planner", elapsed_seconds=3.0))
    orch._persist_timing(TimingRecord(experiment_id="001", agent_type="executor", elapsed_seconds=45.0))
    orch._persist_timing(TimingRecord(experiment_id="001", agent_type="evaluator", elapsed_seconds=9.0))
    orch._persist_timing(TimingRecord(experiment_id="002", agent_type="planner", elapsed_seconds=4.0))

    assert orch._agent_timing["planner"] == [3.0, 4.0]
    assert orch._agent_timing["executor"] == [45.0]
    assert orch._agent_timing["evaluator"] == [9.0]

    records = read_timing(foray_dir)
    assert len(records) == 4


@patch("foray.orchestrator.dispatch")
@patch("foray.orchestrator.build_synthesizer_context", return_value="ctx")
def test_synthesis_includes_timing_stats(mock_ctx, mock_dispatch, tmp_path):
    """Synthesis prompt includes aggregate timing stats per agent type (GH-8)."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0
    orch._prompt_cache = {"synthesizer": "synth prompt"}

    append_timing(foray_dir, TimingRecord(experiment_id="001", agent_type="planner", elapsed_seconds=3.0))
    append_timing(foray_dir, TimingRecord(experiment_id="002", agent_type="planner", elapsed_seconds=4.0))
    append_timing(foray_dir, TimingRecord(experiment_id="001", agent_type="executor", elapsed_seconds=45.0))
    append_timing(foray_dir, TimingRecord(experiment_id="001", agent_type="evaluator", elapsed_seconds=9.0))

    synthesis_path = foray_dir / "synthesis.md"

    def write_synthesis(prompt, **kwargs):
        synthesis_path.write_text("# Report")
        return DispatchResult(exit_code=0, stdout="", stderr="", elapsed_seconds=10.0)

    mock_dispatch.side_effect = write_synthesis
    orch._run_synthesis()

    prompt_arg = mock_dispatch.call_args.kwargs["prompt"]
    assert "planner" in prompt_arg
    assert "executor" in prompt_arg
    assert "evaluator" in prompt_arg
    assert "7s" in prompt_arg   # planner total: 3 + 4 = 7
    assert "45s" in prompt_arg  # executor total
    assert "9s" in prompt_arg   # evaluator total


# --- Discarded hypotheses tracking (foray-dhu) ---


def test_apply_result_adds_discarded_on_failed(tmp_path):
    """FAILED experiment with divergence_note appends to discarded_hypotheses."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir

    from foray.state import write_paths
    path = PathInfo(id="a", description="test", priority=Priority.HIGH, hypothesis="test")
    write_paths(foray_dir, [path])

    result = ExperimentResult(
        experiment_id="001", path_id="a",
        exp_status=ExperimentStatus.FAILED,
        finding=_finding("001", "a", ExperimentStatus.FAILED),
        assessment=_assessment(
            path_status=PathStatus.OPEN,
            confidence=Confidence.MEDIUM,
            divergence_note="OpenCV approach failed due to noise",
        ),
    )
    orch._apply_experiment_result(result)

    from foray.state import read_paths as rp
    updated = next(p for p in rp(foray_dir) if p.id == "a")
    assert "OpenCV approach failed due to noise" in updated.discarded_hypotheses


def test_apply_result_adds_discarded_on_diverged(tmp_path):
    """Diverged hypothesis_alignment appends to discarded_hypotheses."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir

    from foray.state import write_paths
    path = PathInfo(id="a", description="test", priority=Priority.HIGH, hypothesis="test")
    write_paths(foray_dir, [path])

    result = ExperimentResult(
        experiment_id="001", path_id="a",
        exp_status=ExperimentStatus.PARTIAL,
        finding=_finding("001", "a", ExperimentStatus.PARTIAL),
        assessment=_assessment(
            path_status=PathStatus.OPEN,
            confidence=Confidence.MEDIUM,
            hypothesis_alignment="diverged",
            divergence_note="Answered a different question entirely",
        ),
    )
    orch._apply_experiment_result(result)

    from foray.state import read_paths as rp
    updated = next(p for p in rp(foray_dir) if p.id == "a")
    assert "Answered a different question entirely" in updated.discarded_hypotheses


def test_apply_result_no_discard_on_success(tmp_path):
    """SUCCESS experiment with aligned hypothesis does NOT add discarded."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir

    from foray.state import write_paths
    path = PathInfo(id="a", description="test", priority=Priority.HIGH, hypothesis="test")
    write_paths(foray_dir, [path])

    result = ExperimentResult(
        experiment_id="001", path_id="a",
        exp_status=ExperimentStatus.SUCCESS,
        finding=_finding("001", "a"),
        assessment=_assessment(
            path_status=PathStatus.OPEN,
            confidence=Confidence.HIGH,
            hypothesis_alignment="aligned",
        ),
    )
    orch._apply_experiment_result(result)

    from foray.state import read_paths as rp
    updated = next(p for p in rp(foray_dir) if p.id == "a")
    assert updated.discarded_hypotheses == []


def test_apply_result_no_duplicate_discards(tmp_path):
    """Same divergence note from two experiments should not duplicate."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir

    from foray.state import write_paths
    path = PathInfo(id="a", description="test", priority=Priority.HIGH, hypothesis="test")
    write_paths(foray_dir, [path])

    for exp_id in ("001", "002"):
        result = ExperimentResult(
            experiment_id=exp_id, path_id="a",
            exp_status=ExperimentStatus.FAILED,
            finding=_finding(exp_id, "a", ExperimentStatus.FAILED),
            assessment=_assessment(
                path_status=PathStatus.OPEN,
                confidence=Confidence.MEDIUM,
                divergence_note="Same failure reason",
            ),
        )
        orch._apply_experiment_result(result)

    from foray.state import read_paths as rp
    updated = next(p for p in rp(foray_dir) if p.id == "a")
    assert updated.discarded_hypotheses.count("Same failure reason") == 1


def test_apply_result_uses_summary_when_no_divergence_note(tmp_path):
    """INFEASIBLE experiment with empty divergence_note falls back to assessment summary."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir

    from foray.state import write_paths
    path = PathInfo(id="a", description="test", priority=Priority.HIGH, hypothesis="test")
    write_paths(foray_dir, [path])

    result = ExperimentResult(
        experiment_id="001", path_id="a",
        exp_status=ExperimentStatus.INFEASIBLE,
        finding=_finding("001", "a", ExperimentStatus.INFEASIBLE),
        assessment=Evaluation(
            experiment_id="001", path_id="a", outcome="infeasible",
            path_status=PathStatus.OPEN, confidence=Confidence.LOW,
            summary="Dependency X not available in this environment",
        ),
    )
    orch._apply_experiment_result(result)

    from foray.state import read_paths as rp
    updated = next(p for p in rp(foray_dir) if p.id == "a")
    assert "Dependency X not available in this environment" in updated.discarded_hypotheses


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


def test_strategist_fires_when_no_open_paths(tmp_path):
    """_run_strategist dispatches even when all paths are resolved."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)

    from foray.state import write_paths
    write_paths(foray_dir, [
        PathInfo(id="a", description="test", priority=Priority.HIGH, hypothesis="h", status=PathStatus.RESOLVED),
    ])
    # Strategist needs a vision file and agent prompt
    (foray_dir / "vision.md").write_text("test vision")
    (foray_dir / "agents").mkdir(exist_ok=True)
    (foray_dir / "agents" / "strategist.md").write_text("test prompt")

    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0

    mock_result = DispatchResult(
        exit_code=0, stdout="", stderr="", elapsed_seconds=1.0,
        input_tokens=0, output_tokens=0, cost_usd=0.0,
    )
    with patch("foray.orchestrator.dispatch", return_value=mock_result) as mock_dispatch:
        orch._run_strategist(1)
        mock_dispatch.assert_called_once()


def test_strategist_skips_when_budget_exhausted(tmp_path):
    """_run_strategist is a no-op when experiment budget is exhausted."""
    config = RunConfig(vision_path="vision.md", max_experiments=10)
    state = RunState(
        start_time=datetime.now(timezone.utc), config=config,
        experiment_count=10,
    )
    foray_dir = init_directory(tmp_path, state)

    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0

    # Should not dispatch — no budget remaining
    orch._run_strategist(1)  # no error = skipped
