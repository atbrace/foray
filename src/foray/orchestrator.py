from __future__ import annotations

import logging
import shutil
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import click

from foray.context import (
    build_evaluator_context,
    build_exhaustion_evaluator_context,
    build_executor_context,
    build_planner_context,
    build_strategist_context,
    build_synthesizer_context,
)
from foray.dispatcher import (
    dispatch,
    dispatch_executor,
    parse_experiment_status,
    parse_stream_json_tokens,
    write_crash_stub,
    write_planner_crash_stub,
)
from foray.environment import run_preflight
from foray.models import (
    Confidence,
    DispatchResult,
    Evaluation,
    ExperimentResult,
    ExperimentStatus,
    Finding,
    PathInfo,
    PathStatus,
    Round,
    RoundOutcome,
    RunConfig,
    RunState,
    StrategyOutput,
    TimingRecord,
)
from foray.permissions import resolve_tools
from foray.scheduler import (
    check_consecutive_failures,
    check_path_failure_threshold,
    get_round_paths,
    needs_concentration_justification,
    next_experiment_id,
    should_continue,
)
from foray.state import (
    add_finding,
    append_timing,
    init_directory,
    read_evaluation,
    read_findings,
    read_paths,
    read_rounds,
    read_run_state,
    read_strategy,
    read_timing,
    write_paths,
    write_rounds,
    write_run_state,
)
from foray.worktree import (
    cleanup_worktree,
    copy_artifacts,
    create_worktree,
    enforce_worktree_limit,
    prune_worktrees,
    should_preserve_worktree,
)

logger = logging.getLogger(__name__)

_print_lock = threading.Lock()

_STATUS_SYMBOLS = {
    "SUCCESS": "✓",
    "PARTIAL": "~",
    "FAILED": "✗",
    "INFEASIBLE": "—",
    "CRASH": "!",
    "EXHAUSTED": "∅",
}


def _format_seconds(secs: float) -> str:
    """Format a duration in seconds as a human-readable string."""
    if secs < 60:
        return f"{secs:.0f}s"
    mins = secs / 60
    if mins < 60:
        return f"{mins:.1f}m"
    return f"{mins / 60:.1f}h"


def _elapsed_str(start: float) -> str:
    """Format elapsed time since start as a human-readable string."""
    return _format_seconds(time.monotonic() - start)


def _log(msg: str, start: float | None = None) -> None:
    """Print a timestamped progress line (thread-safe)."""
    ts = datetime.now().strftime("%H:%M:%S")
    elapsed = f" ({_elapsed_str(start)})" if start is not None else ""
    with _print_lock:
        click.echo(f"[{ts}]{elapsed} {msg}")


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

        # EXHAUSTED: relax to 1 — prevents loops where evaluator unanimously recommends resolution but guardrails keep rejecting
        min_experiments = 1 if exp_status == ExperimentStatus.EXHAUSTED else 2
        if non_failures < min_experiments and not has_independent_override:
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

    # Environment failure escalation: 2+ FAILED experiments → stop retrying
    if recommended == PathStatus.OPEN:
        env_failures = sum(
            1 for f in findings
            if f.path_id == path.id and f.status == ExperimentStatus.FAILED
        )
        if env_failures >= 2:
            logger.info(
                f"Guardrail: escalating '{path.id}' to inconclusive "
                f"-- {env_failures} environment failures"
            )
            return PathStatus.INCONCLUSIVE

    return recommended


def _crash_result(experiment_id: str, path_id: str, summary: str) -> ExperimentResult:
    """Build a CRASH ExperimentResult with a minimal Finding."""
    return ExperimentResult(
        experiment_id=experiment_id,
        path_id=path_id,
        exp_status=ExperimentStatus.CRASH,
        finding=Finding(
            experiment_id=experiment_id,
            path_id=path_id,
            status=ExperimentStatus.CRASH,
            summary=summary,
        ),
    )


class Orchestrator:
    def __init__(self, project_root: Path, config: RunConfig):
        self.project_root = project_root
        self.config = config
        self.foray_dir = project_root / config.output_dir
        self.tools = resolve_tools(config.allow_tools, config.deny_tools)
        self._prompt_cache: dict[str, str] = {}
        self._prompt_cache_lock = threading.Lock()
        self._agent_timing: dict[str, list[float]] = {}
        self._timing_lock = threading.Lock()

    def _persist_timing(self, record: TimingRecord) -> None:
        """Record timing in-memory and persist to disk (thread-safe)."""
        with self._timing_lock:
            self._agent_timing.setdefault(record.agent_type, []).append(record.elapsed_seconds)
            append_timing(self.foray_dir, record)

    def _record_dispatch(
        self, result: DispatchResult, experiment_id: str, agent_type: str,
    ) -> None:
        """Parse token usage from dispatch stdout and persist timing."""
        tokens = parse_stream_json_tokens(result.stdout)
        self._persist_timing(TimingRecord(
            experiment_id=experiment_id,
            agent_type=agent_type,
            elapsed_seconds=result.elapsed_seconds,
            input_tokens=tokens["input_tokens"],
            output_tokens=tokens["output_tokens"],
            cost_usd=tokens["cost_usd"],
        ))

    def init(self) -> Path:
        """Initialize .foray/ directory, dispatch initializer, return foray_dir."""
        self._run_start = time.monotonic()
        state = RunState(
            start_time=datetime.now(timezone.utc),
            config=self.config,
        )
        self.foray_dir = init_directory(self.project_root, state)

        # Copy vision doc
        vision_src = Path(self.config.vision_path)
        if not vision_src.is_absolute():
            vision_src = self.project_root / vision_src
        shutil.copy2(vision_src, self.foray_dir / "vision.md")

        self._install_agent_prompts()
        self._ensure_gitignore()

        # Dispatch initializer
        _log("Initializing — analyzing codebase and identifying paths...")
        agent_start = time.monotonic()
        template = self._load_agent_prompt("initializer")
        vision = (self.foray_dir / "vision.md").read_text()
        prompt = (
            f"{template}\n\n---\n\n"
            f"# Vision\n\n{vision}\n\n---\n\n"
            f"Write codebase map to: {self.foray_dir / 'codebase_map.md'}\n"
            f"Write paths JSON to: {self.foray_dir / 'state' / 'paths.json'}\n"
            f"Write paths summary to: {self.foray_dir / 'paths_summary.md'}\n"
        )
        result = dispatch(
            prompt=prompt,
            workdir=self.project_root,
            model=self.config.model,
            max_turns=self.config.max_turns,
            tools=["Read", "Glob", "Grep", "Bash", "Write"],
            output_format="stream-json",
        )
        self._record_dispatch(result, "init", "initializer")
        if result.exit_code != 0:
            _log(f"Initializer failed (exit {result.exit_code})")
            if result.stderr:
                click.echo(f"stderr: {result.stderr[:1000]}", err=True)
            if result.stdout:
                click.echo(f"stdout (last 500 chars): ...{result.stdout[-500:]}", err=True)
            raise RuntimeError(f"Initializer failed (exit {result.exit_code})")
        _log(f"Initialized ({_format_seconds(result.elapsed_seconds)})", agent_start)

        run_preflight(self.foray_dir, self.project_root)
        _log("Environment pre-flight complete", self._run_start)

        return self.foray_dir

    def run(self) -> None:
        """Main exploration loop: rounds until budget exhausted or stop file."""
        if not hasattr(self, "_run_start"):
            self._run_start = time.monotonic()

        try:
            while True:
                state = read_run_state(self.foray_dir)
                paths = read_paths(self.foray_dir)
                findings = read_findings(self.foray_dir)

                if not should_continue(state, paths, (self.foray_dir / ".stop").exists()):
                    break
                if check_consecutive_failures(findings):
                    _log("Circuit breaker: 3 consecutive failures — stopping early",
                         self._run_start)
                    break

                round_paths = get_round_paths(paths)
                if not round_paths:
                    break

                round_num = state.current_round + 1

                # Trim to remaining budget
                remaining_budget = state.config.max_experiments - state.experiment_count
                round_paths = round_paths[:remaining_budget]

                open_n = sum(1 for p in paths if p.status == PathStatus.OPEN)
                _log(
                    f"Round {round_num}: {len(round_paths)} experiment(s), "
                    f"{open_n} path(s) open, "
                    f"{state.experiment_count}/{state.config.max_experiments} budget used",
                    self._run_start,
                )
                current_round = Round(
                    round_number=round_num,
                    paths=round_paths,
                    started_at=datetime.now(timezone.utc),
                )

                # Pre-assign experiment IDs
                base_count = state.experiment_count
                experiments_to_run: list[tuple[str, PathInfo]] = []
                for i, path_id in enumerate(round_paths):
                    path = next(p for p in paths if p.id == path_id)
                    experiment_id = next_experiment_id(base_count + i)
                    experiments_to_run.append((experiment_id, path))
                    _log(f"  {experiment_id} | path: {path_id}")

                state.current_round = round_num
                write_run_state(self.foray_dir, state)

                # Prune stale worktree refs once per round (not per experiment)
                prune_worktrees(self.project_root)

                # Pre-warm prompt cache
                for name in ("planner", "executor", "evaluator"):
                    self._load_agent_prompt(name)

                # Phase 1: Parallel dispatch
                findings_snapshot = list(findings)
                state_snapshot = state
                results: list[ExperimentResult] = []

                max_workers = min(len(experiments_to_run), self.config.max_concurrent)
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    futures = {
                        pool.submit(
                            self._run_experiment, path, exp_id,
                            list(findings_snapshot), state_snapshot,
                        ): (exp_id, path.id)
                        for exp_id, path in experiments_to_run
                    }
                    for future in as_completed(futures):
                        exp_id, path_id = futures[future]
                        try:
                            results.append(future.result())
                        except Exception as exc:
                            logger.error(f"Unexpected error in {exp_id}: {exc}")
                            results.append(_crash_result(exp_id, path_id, f"Unhandled: {exc}"))

                # Sort by experiment_id for deterministic merge order
                results.sort(key=lambda r: r.experiment_id)

                # Phase 2: Sequential merge
                for result in results:
                    updated_path = self._apply_experiment_result(result)
                    current_path = updated_path or next(
                        (p for p in read_paths(self.foray_dir) if p.id == result.path_id), None
                    )
                    current_round.outcomes.append(RoundOutcome(
                        path_id=result.path_id,
                        experiment_id=result.experiment_id,
                        status=result.exp_status,
                        path_status_after=current_path.status if current_path else PathStatus.OPEN,
                        started_at=result.started_at,
                        completed_at=result.completed_at,
                        elapsed_seconds=result.elapsed_seconds,
                    ))

                    symbol = _STATUS_SYMBOLS.get(result.exp_status.value, "?")
                    status_change = ""
                    if current_path and current_path.status != PathStatus.OPEN:
                        status_change = f" → path {current_path.status.value}"
                    _log(f"  {result.experiment_id} | {symbol} {result.exp_status.value}{status_change}",
                         self._run_start)

                    all_findings = read_findings(self.foray_dir)
                    if check_path_failure_threshold(result.path_id, all_findings):
                        _log(f"  {result.path_id}: hit failure threshold — marking blocked")
                        paths_now = read_paths(self.foray_dir)
                        write_paths(self.foray_dir, [
                            p.model_copy(update={"status": PathStatus.BLOCKED})
                            if p.id == result.path_id else p
                            for p in paths_now
                        ])

                current_round.completed_at = datetime.now(timezone.utc)
                rounds = read_rounds(self.foray_dir)
                rounds.append(current_round)
                write_rounds(self.foray_dir, rounds)
                logger.info(f"Round {round_num} complete: {len(round_paths)} experiments")

                # Phase 3: Strategic review
                self._run_strategist(round_num)
        finally:
            self._run_synthesis()

    def _run_experiment(
        self, path: PathInfo, experiment_id: str, findings: list[Finding],
        state: RunState,
    ) -> ExperimentResult:
        """Plan -> execute -> assess one experiment. Returns result for deferred merge."""
        try:
            return self._run_experiment_inner(path, experiment_id, findings, state)
        except Exception as e:
            logger.error(f"Experiment {experiment_id} crashed: {e}", exc_info=True)
            return _crash_result(experiment_id, path.id, f"Experiment crashed: {e}")

    def _cleanup_prebuilt_worktree(self, future: Future) -> None:
        """Clean up a pre-created worktree when the experiment exits early."""
        try:
            wt_path = future.result(timeout=30)
            cleanup_worktree(self.project_root, wt_path)
        except Exception as e:
            logger.warning(f"Failed to clean up pre-built worktree: {e}")

    def _run_experiment_inner(
        self, path: PathInfo, experiment_id: str, findings: list[Finding],
        state: RunState,
    ) -> ExperimentResult:
        """Inner experiment logic — may raise."""
        exp_started_at = datetime.now(timezone.utc)
        path_findings = [f for f in findings if f.path_id == path.id]

        # Pre-create worktree in background (overlaps with planning)
        worktree_pool = ThreadPoolExecutor(max_workers=1)
        worktree_future = worktree_pool.submit(
            create_worktree, self.project_root, experiment_id, self.foray_dir,
        )

        # --- Plan ---
        _log(f"    {experiment_id} planning...", self._run_start)
        planner_ctx = build_planner_context(
            self.foray_dir, path, path_findings, state,
            needs_concentration_justification(path.id, findings),
        )
        planner_template = self._load_agent_prompt("planner")
        plan_path = self.foray_dir / "experiments" / f"{experiment_id}_plan.md"
        planner_attempts: list[DispatchResult] = []
        planner_result = dispatch(
            prompt=(
                f"{planner_template}\n\n---\n\n{planner_ctx}\n\n---\n\n"
                f"Write experiment plan to: {plan_path}"
            ),
            workdir=self.project_root,
            model=self.config.model,
            max_turns=self.config.max_turns,
            tools=["Read", "Glob", "Grep", "Write"],
            output_format="stream-json",
        )
        planner_attempts.append(planner_result)
        self._record_dispatch(planner_result, experiment_id, "planner")
        _log(f"    {experiment_id} planned ({_format_seconds(planner_result.elapsed_seconds)})", self._run_start)

        if not plan_path.exists():
            logger.warning(
                f"{experiment_id} planner attempt 1 failed: "
                f"exit_code={planner_result.exit_code}, "
                f"stderr={planner_result.stderr[:200]!r}"
            )
            _log(f"    {experiment_id} planner produced no plan — retrying simplified", self._run_start)
            retry_result = dispatch(
                prompt=(
                    f"{planner_template}\n\nPath: {path.id}\n"
                    f"Description: {path.description}\n\n"
                    f"Write a simple experiment plan to: {plan_path}"
                ),
                workdir=self.project_root,
                model=self.config.model,
                max_turns=self.config.max_turns,
                tools=["Read", "Glob", "Grep", "Write"],
                output_format="stream-json",
            )
            planner_attempts.append(retry_result)
            self._record_dispatch(retry_result, experiment_id, "planner")
            if not plan_path.exists():
                _log(f"    {experiment_id} planner failed twice — marking CRASH", self._run_start)
                write_planner_crash_stub(
                    self.foray_dir, experiment_id, path.id, planner_attempts,
                )
                self._cleanup_prebuilt_worktree(worktree_future)
                worktree_pool.shutdown(wait=False)
                return _crash_result(
                    experiment_id, path.id,
                    "Planner failed to produce a plan after two attempts",
                )

        # --- Check for exhaustion signal ---
        plan_content = plan_path.read_text()
        if any(line.strip() == "## Status: EXHAUSTED" for line in plan_content.splitlines()[:5]):
            _log(f"    {experiment_id} planner signaled exhaustion — routing to evaluator", self._run_start)
            lines = plan_content.split("\n")
            rationale_lines = []
            capture = False
            for line in lines:
                if line.strip() == "## Rationale":
                    capture = True
                    continue
                if capture:
                    if line.startswith("## "):
                        break
                    rationale_lines.append(line)
            rationale_text = "\n".join(rationale_lines).strip()

            assessor_template = self._load_agent_prompt("evaluator")
            assessor_ctx = build_exhaustion_evaluator_context(
                self.foray_dir, path, path_findings, rationale_text,
            )
            assessment_path = self.foray_dir / "experiments" / f"{experiment_id}_eval.json"
            exhaust_eval_result = dispatch(
                prompt=(
                    f"{assessor_template}\n\n---\n\n{assessor_ctx}\n\n---\n\n"
                    f"Write assessment JSON to: {assessment_path}\n"
                    f"Use experiment_id: {experiment_id}"
                ),
                workdir=self.project_root,
                model=self.config.evaluator_model,
                max_turns=6,
                tools=["Read", "Write"],
                output_format="stream-json",
            )
            self._record_dispatch(exhaust_eval_result, experiment_id, "evaluator")
            _log(f"    {experiment_id} evaluated ({_format_seconds(exhaust_eval_result.elapsed_seconds)})", self._run_start)
            assessment = read_evaluation(self.foray_dir, experiment_id)
            finding_summary = assessment.summary if assessment else rationale_text[:200]

            self._cleanup_prebuilt_worktree(worktree_future)
            worktree_pool.shutdown(wait=False)
            exp_completed_at = datetime.now(timezone.utc)
            return ExperimentResult(
                experiment_id=experiment_id,
                path_id=path.id,
                exp_status=ExperimentStatus.EXHAUSTED,
                finding=Finding(
                    experiment_id=experiment_id,
                    path_id=path.id,
                    status=ExperimentStatus.EXHAUSTED,
                    summary=finding_summary,
                    one_liner=f"Path exhausted: {rationale_text[:80]}",
                    planner_brief=assessment.planner_brief if assessment else "",
                ),
                assessment=assessment,
                started_at=exp_started_at,
                completed_at=exp_completed_at,
                elapsed_seconds=(exp_completed_at - exp_started_at).total_seconds(),
            )

        # --- Execute ---
        _log(f"    {experiment_id} executing...", self._run_start)
        worktree_path = worktree_future.result()
        worktree_pool.shutdown(wait=False)
        results_path = self.foray_dir / "experiments" / f"{experiment_id}_results.md"
        artifacts_dir = self.foray_dir / "experiments" / f"{experiment_id}_artifacts"

        executor_template = self._load_agent_prompt("executor")
        executor_ctx = build_executor_context(self.foray_dir, plan_path)
        exec_result = dispatch_executor(
            prompt=(
                f"{executor_template}\n\n---\n\n{executor_ctx}\n\n---\n\n"
                f"Write results to: {results_path}\n"
                f"Your worktree is at: {worktree_path}\n"
                f"Save artifacts to: {artifacts_dir}"
            ),
            worktree_path=worktree_path,
            project_root=self.project_root,
            experiment_id=experiment_id,
            model=self.config.model,
            max_turns=self.config.max_turns,
            tools=self.tools,
            foray_dir=self.foray_dir,
        )
        self._record_dispatch(exec_result, experiment_id, "executor")
        _log(f"    {experiment_id} executed ({_format_seconds(exec_result.elapsed_seconds)})", self._run_start)

        if not results_path.exists():
            write_crash_stub(
                self.foray_dir, experiment_id, plan_path, exec_result,
            )

        exp_status = parse_experiment_status(results_path)

        # --- Assess ---
        assessment = None
        if exp_status == ExperimentStatus.CRASH:
            _log(f"    {experiment_id} skipping evaluation (executor crashed)", self._run_start)
            finding_summary = "Executor crashed — no results to evaluate"
        else:
            _log(f"    {experiment_id} evaluating...", self._run_start)
            assessor_template = self._load_agent_prompt("evaluator")
            assessor_ctx = build_evaluator_context(self.foray_dir, experiment_id, path, path_findings)
            assessment_path = self.foray_dir / "experiments" / f"{experiment_id}_eval.json"
            eval_result = dispatch(
                prompt=(
                    f"{assessor_template}\n\n---\n\n{assessor_ctx}\n\n---\n\n"
                    f"Write assessment JSON to: {assessment_path}\n"
                    f"Use experiment_id: {experiment_id}"
                ),
                workdir=self.project_root,
                model=self.config.evaluator_model,
                max_turns=6,
                tools=["Read", "Write"],
                output_format="stream-json",
            )
            self._record_dispatch(eval_result, experiment_id, "evaluator")
            _log(f"    {experiment_id} evaluated ({_format_seconds(eval_result.elapsed_seconds)})", self._run_start)
            assessment = read_evaluation(self.foray_dir, experiment_id)
            if assessment:
                finding_summary = assessment.summary
            else:
                stderr_snippet = (eval_result.stderr or "")[:500]
                stdout_snippet = (eval_result.stdout or "")[:500]
                logger.warning(
                    f"Evaluator produced no assessment for {experiment_id} "
                    f"(exit={eval_result.exit_code}): {stderr_snippet}"
                )
                _log(
                    f"    {experiment_id} WARNING: no assessment file written "
                    f"(exit={eval_result.exit_code}, stderr={stderr_snippet!r}, "
                    f"stdout={stdout_snippet!r})",
                    self._run_start,
                )
                finding_summary = "(assessment failed)"

        # Worktree cleanup
        copy_artifacts(worktree_path, artifacts_dir)
        if not should_preserve_worktree(exp_status):
            cleanup_worktree(self.project_root, worktree_path)
        enforce_worktree_limit(self.foray_dir, self.project_root)

        exp_completed_at = datetime.now(timezone.utc)
        return ExperimentResult(
            experiment_id=experiment_id,
            path_id=path.id,
            exp_status=exp_status,
            finding=Finding(
                experiment_id=experiment_id,
                path_id=path.id,
                status=exp_status,
                summary=finding_summary,
                planner_brief=assessment.planner_brief if assessment else "",
                observations=assessment.observations if assessment else [],
                suggested_next=assessment.new_questions if assessment else [],
            ),
            assessment=assessment,
            started_at=exp_started_at,
            completed_at=exp_completed_at,
            elapsed_seconds=(exp_completed_at - exp_started_at).total_seconds(),
        )

    def _apply_experiment_result(self, result: ExperimentResult) -> PathInfo | None:
        """Apply one experiment's results to shared state. Must be called sequentially."""
        add_finding(self.foray_dir, result.finding)

        updated_path = None
        if result.assessment:
            all_findings = read_findings(self.foray_dir)
            paths = read_paths(self.foray_dir)
            path = next((p for p in paths if p.id == result.path_id), None)
            if path:
                new_status = apply_guardrails(result.assessment, path, all_findings, result.exp_status)
                new_discarded = list(path.discarded_hypotheses)
                if (
                    result.exp_status in (ExperimentStatus.FAILED, ExperimentStatus.INFEASIBLE)
                    or result.assessment.hypothesis_alignment == "diverged"
                ):
                    note = result.assessment.divergence_note or result.assessment.summary
                    if note and note not in new_discarded:
                        new_discarded.append(note)
                write_paths(self.foray_dir, [
                    p.model_copy(update={
                        "status": new_status,
                        "experiment_count": p.experiment_count + 1,
                        "topic_tags": list(set(p.topic_tags + result.assessment.topic_tags)),
                        "discarded_hypotheses": new_discarded,
                    }) if p.id == result.path_id else p
                    for p in paths
                ])
                updated_path = next(
                    (p for p in read_paths(self.foray_dir) if p.id == result.path_id), None
                )

        state = read_run_state(self.foray_dir)
        state.experiment_count += 1
        state.last_completed_experiment = result.experiment_id
        write_run_state(self.foray_dir, state)

        return updated_path

    def _apply_strategy(self, strategy: StrategyOutput) -> None:
        """Apply strategist decisions to paths. Respects evaluator-resolved paths."""
        paths = read_paths(self.foray_dir)

        for decision in strategy.decisions:
            if decision.action == "close":
                for i, p in enumerate(paths):
                    if p.id == decision.path_id:
                        if p.status == PathStatus.RESOLVED:
                            logger.info(f"Strategist: skipping close of '{p.id}' -- already resolved by evaluator")
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
        if round_num <= 1:
            return

        state = read_run_state(self.foray_dir)
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

    def _format_timing_stats(self) -> str:
        """Format aggregate timing stats from persisted timing records."""
        records = read_timing(self.foray_dir)
        if not records:
            return ""
        by_type: dict[str, list[TimingRecord]] = {}
        for r in records:
            by_type.setdefault(r.agent_type, []).append(r)
        lines = ["## Agent Timing Stats"]
        total_cost = sum(r.cost_usd for r in records)
        total_tokens_in = sum(r.input_tokens for r in records)
        total_tokens_out = sum(r.output_tokens for r in records)
        for agent_type in sorted(by_type):
            recs = by_type[agent_type]
            total = sum(r.elapsed_seconds for r in recs)
            count = len(recs)
            avg = total / count
            agent_in = sum(r.input_tokens for r in recs)
            agent_out = sum(r.output_tokens for r in recs)
            token_info = f", {agent_in:,} in / {agent_out:,} out" if agent_in or agent_out else ""
            lines.append(
                f"- {agent_type}: {count} call(s), "
                f"{_format_seconds(total)} total, "
                f"{_format_seconds(avg)} avg{token_info}"
            )
        if total_tokens_in > 0 or total_tokens_out > 0:
            lines.append(f"\n**Token usage:** {total_tokens_in:,} input, {total_tokens_out:,} output")
        if total_cost > 0:
            lines.append(f"**Estimated cost:** ${total_cost:.2f}")
        return "\n".join(lines)

    def _run_synthesis(self) -> None:
        _log("Synthesizing final report...", self._run_start)
        template = self._load_agent_prompt("synthesizer")
        ctx = build_synthesizer_context(self.foray_dir)
        synthesis_path = self.foray_dir / "synthesis.md"
        timing_stats = self._format_timing_stats()
        prompt = (
            f"{template}\n\n---\n\n{ctx}\n\n---\n\n"
            f"Write synthesis report to: {synthesis_path}\n"
            f"Read individual results from: {self.foray_dir / 'experiments'}/"
        )
        if timing_stats:
            prompt += f"\n\n{timing_stats}"

        for attempt in range(2):
            result = dispatch(
                prompt=prompt,
                workdir=self.project_root,
                model=self.config.model,
                max_turns=self.config.max_turns,
                tools=["Read", "Glob", "Write"],
                output_format="stream-json",
            )
            self._record_dispatch(result, "synthesis", "synthesizer")
            if synthesis_path.exists():
                return
            stderr_snippet = (result.stderr or "")[:500]
            _log(
                f"Synthesizer attempt {attempt + 1} failed "
                f"(exit={result.exit_code}): {stderr_snippet}",
                self._run_start,
            )

        logger.warning("Synthesis failed after 2 attempts — no report generated")

    def _install_agent_prompts(self) -> None:
        bundled_dir = Path(__file__).parent / "agents"
        if not bundled_dir.exists():
            return
        target_dir = self.foray_dir / "agents"
        for prompt_file in bundled_dir.glob("*.md"):
            target = target_dir / prompt_file.name
            if not target.exists():
                shutil.copy2(prompt_file, target)

    def _load_agent_prompt(self, agent_name: str) -> str:
        if agent_name in self._prompt_cache:
            return self._prompt_cache[agent_name]
        with self._prompt_cache_lock:
            if agent_name in self._prompt_cache:
                return self._prompt_cache[agent_name]
            local = self.foray_dir / "agents" / f"{agent_name}.md"
            if local.exists():
                content = local.read_text()
            else:
                content = (Path(__file__).parent / "agents" / f"{agent_name}.md").read_text()
            self._prompt_cache[agent_name] = content
        return content

    def _ensure_gitignore(self) -> None:
        gitignore = self.project_root / ".gitignore"
        entry = ".foray/"
        if gitignore.exists():
            content = gitignore.read_text()
            if entry not in content.splitlines():
                gitignore.write_text(content.rstrip() + f"\n{entry}\n")
        else:
            gitignore.write_text(f"{entry}\n")
