from __future__ import annotations

import logging
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import click

from foray.context import (
    build_evaluator_context,
    build_executor_context,
    build_planner_context,
    build_synthesizer_context,
)
from foray.dispatcher import (
    DEFAULT_TIMEOUT_MINUTES,
    dispatch,
    dispatch_executor,
    parse_experiment_status,
    write_crash_stub,
)
from foray.models import (
    Confidence,
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
)


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
            one_liner=summary[:100],
        ),
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
    init_directory,
    read_evaluation,
    read_findings,
    read_paths,
    read_rounds,
    read_run_state,
    write_paths,
    write_rounds,
    write_run_state,
)
from foray.worktree import (
    cleanup_worktree,
    copy_artifacts,
    create_worktree,
    enforce_worktree_limit,
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
}


def _elapsed_str(start: float) -> str:
    """Format elapsed time since start as a human-readable string."""
    secs = time.monotonic() - start
    if secs < 60:
        return f"{secs:.0f}s"
    mins = secs / 60
    if mins < 60:
        return f"{mins:.1f}m"
    return f"{mins / 60:.1f}h"


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
) -> PathStatus:
    """Apply deterministic guardrails to assessor's status recommendation.

    - Resolved requires 2+ non-failure experiments and at least medium confidence.
    - Blocked requires a non-empty blocker description.
    """
    recommended = assessment.path_status

    if recommended == PathStatus.RESOLVED:
        non_failures = sum(
            1 for f in findings
            if f.path_id == path.id
            and f.status in (ExperimentStatus.SUCCESS, ExperimentStatus.PARTIAL)
        )
        if non_failures < 2:
            logger.info(
                f"Guardrail: rejecting resolution of '{path.id}' "
                f"-- only {non_failures} non-failure experiment(s)"
            )
            return PathStatus.OPEN
        if assessment.confidence == Confidence.LOW:
            logger.info(f"Guardrail: rejecting resolution of '{path.id}' -- low confidence")
            return PathStatus.OPEN

    if recommended == PathStatus.BLOCKED and not assessment.blocker_description:
        logger.info(f"Guardrail: rejecting block of '{path.id}' -- no blocker description")
        return PathStatus.OPEN

    return recommended


class Orchestrator:
    def __init__(self, project_root: Path, config: RunConfig):
        self.project_root = project_root
        self.config = config
        self.foray_dir = project_root / config.output_dir
        self.tools = resolve_tools(config.allow_tools, config.deny_tools)
        self._prompt_cache: dict[str, str] = {}
        self._prompt_cache_lock = threading.Lock()

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
        )
        if result.exit_code != 0:
            _log(f"Initializer failed (exit {result.exit_code})")
            if result.stderr:
                click.echo(f"stderr: {result.stderr[:1000]}", err=True)
            if result.stdout:
                click.echo(f"stdout (last 500 chars): ...{result.stdout[-500:]}", err=True)
            raise RuntimeError(f"Initializer failed (exit {result.exit_code})")
        _log("Initialization complete", agent_start)

        return self.foray_dir

    def run(self) -> None:
        """Main exploration loop: rounds until budget exhausted or stop file."""
        if not hasattr(self, "_run_start"):
            self._run_start = time.monotonic()

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

    def _run_experiment_inner(
        self, path: PathInfo, experiment_id: str, findings: list[Finding],
        state: RunState,
    ) -> ExperimentResult:
        """Inner experiment logic — may raise."""
        path_findings = [f for f in findings if f.path_id == path.id]

        # --- Plan ---
        _log(f"    {experiment_id} planning...", self._run_start)
        planner_ctx = build_planner_context(
            self.foray_dir, path, path_findings, state,
            needs_concentration_justification(path.id, findings),
        )
        planner_template = self._load_agent_prompt("planner")
        plan_path = self.foray_dir / "experiments" / f"{experiment_id}_plan.md"
        dispatch(
            prompt=(
                f"{planner_template}\n\n---\n\n{planner_ctx}\n\n---\n\n"
                f"Write experiment plan to: {plan_path}"
            ),
            workdir=self.project_root,
            model=self.config.model,
            max_turns=self.config.max_turns,
            tools=["Read", "Glob", "Grep", "Write"],
        )

        if not plan_path.exists():
            _log(f"    {experiment_id} planner produced no plan — retrying simplified", self._run_start)
            dispatch(
                prompt=(
                    f"{planner_template}\n\nPath: {path.id}\n"
                    f"Description: {path.description}\n\n"
                    f"Write a simple experiment plan to: {plan_path}"
                ),
                workdir=self.project_root,
                model=self.config.model,
                max_turns=self.config.max_turns,
                tools=["Read", "Glob", "Grep", "Write"],
            )
            if not plan_path.exists():
                _log(f"    {experiment_id} planner failed twice — marking CRASH", self._run_start)
                return _crash_result(
                    experiment_id, path.id,
                    "Planner failed to produce a plan after two attempts",
                )

        # --- Execute ---
        _log(f"    {experiment_id} executing...", self._run_start)
        worktree_path = create_worktree(self.project_root, experiment_id, self.foray_dir)
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

        if not results_path.exists():
            write_crash_stub(
                self.foray_dir, experiment_id, plan_path, exec_result,
                timeout_minutes=DEFAULT_TIMEOUT_MINUTES,
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
            dispatch(
                prompt=(
                    f"{assessor_template}\n\n---\n\n{assessor_ctx}\n\n---\n\n"
                    f"Write assessment JSON to: {assessment_path}"
                ),
                workdir=self.project_root,
                model=self.config.evaluator_model,
                max_turns=6,
                tools=["Read", "Write"],
            )
            assessment = read_evaluation(self.foray_dir, experiment_id)
            finding_summary = assessment.summary if assessment else "(assessment failed)"

        # Worktree cleanup
        copy_artifacts(worktree_path, artifacts_dir)
        if not should_preserve_worktree(exp_status):
            cleanup_worktree(self.project_root, worktree_path)
        enforce_worktree_limit(self.foray_dir, self.project_root)

        return ExperimentResult(
            experiment_id=experiment_id,
            path_id=path.id,
            exp_status=exp_status,
            finding=Finding(
                experiment_id=experiment_id,
                path_id=path.id,
                status=exp_status,
                summary=finding_summary,
                one_liner=finding_summary[:100],
                planner_brief=assessment.planner_brief if assessment else "",
            ),
            assessment=assessment,
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
                new_status = apply_guardrails(result.assessment, path, all_findings)
                write_paths(self.foray_dir, [
                    p.model_copy(update={
                        "status": new_status,
                        "experiment_count": p.experiment_count + 1,
                        "topic_tags": list(set(p.topic_tags + result.assessment.topic_tags)),
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

    def _run_synthesis(self) -> None:
        _log("Synthesizing final report...", self._run_start)
        template = self._load_agent_prompt("synthesizer")
        ctx = build_synthesizer_context(self.foray_dir)
        dispatch(
            prompt=(
                f"{template}\n\n---\n\n{ctx}\n\n---\n\n"
                f"Write synthesis report to: {self.foray_dir / 'synthesis.md'}\n"
                f"Read individual results from: {self.foray_dir / 'experiments'}/"
            ),
            workdir=self.project_root,
            model=self.config.model,
            max_turns=self.config.max_turns,
            tools=["Read", "Glob", "Write"],
        )

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
