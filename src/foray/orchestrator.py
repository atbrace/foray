from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from foray.context import (
    build_evaluator_context,
    build_executor_context,
    build_planner_context,
    build_synthesizer_context,
)
from foray.dispatcher import (
    dispatch,
    dispatch_executor,
    parse_experiment_status,
    write_crash_stub,
)
from foray.models import (
    Confidence,
    Evaluation,
    ExperimentStatus,
    Finding,
    PathInfo,
    PathStatus,
    Round,
    RoundOutcome,
    RunConfig,
    RunState,
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

    def init(self) -> Path:
        """Initialize .foray/ directory, dispatch initializer, return foray_dir."""
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
            raise RuntimeError(f"Initializer failed (exit {result.exit_code}): {result.stderr[:500]}")

        return self.foray_dir

    def run(self) -> None:
        """Main exploration loop: rounds until budget exhausted or stop file."""
        while True:
            state = read_run_state(self.foray_dir)
            paths = read_paths(self.foray_dir)
            findings = read_findings(self.foray_dir)

            if not should_continue(state, paths, (self.foray_dir / ".stop").exists()):
                break
            if check_consecutive_failures(findings):
                logger.warning("Circuit breaker: 3 consecutive failures -- early synthesis")
                break

            round_paths = get_round_paths(paths)
            if not round_paths:
                break

            round_num = state.current_round + 1
            current_round = Round(
                round_number=round_num,
                paths=round_paths,
                started_at=datetime.now(timezone.utc),
            )

            for i, path_id in enumerate(round_paths):
                state = read_run_state(self.foray_dir)
                paths = read_paths(self.foray_dir)
                findings = read_findings(self.foray_dir)
                path = next(p for p in paths if p.id == path_id)

                experiment_id = next_experiment_id(state.experiment_count)
                state.current_round = round_num
                state.current_path_index = i
                write_run_state(self.foray_dir, state)

                exp_status = self._run_experiment(path, experiment_id, findings)

                state = read_run_state(self.foray_dir)
                state.experiment_count += 1
                state.last_completed_experiment = experiment_id
                write_run_state(self.foray_dir, state)

                current_path = next((p for p in read_paths(self.foray_dir) if p.id == path_id), path)
                current_round.outcomes.append(RoundOutcome(
                    path_id=path_id,
                    experiment_id=experiment_id,
                    status=exp_status,
                    path_status_after=current_path.status,
                ))

                findings = read_findings(self.foray_dir)
                if check_path_failure_threshold(path_id, findings):
                    logger.warning(f"Path '{path_id}' hit failure threshold -- marking blocked")
                    paths = read_paths(self.foray_dir)
                    write_paths(self.foray_dir, [
                        p.model_copy(update={"status": PathStatus.BLOCKED})
                        if p.id == path_id else p
                        for p in paths
                    ])

            current_round.completed_at = datetime.now(timezone.utc)
            rounds = read_rounds(self.foray_dir)
            rounds.append(current_round)
            write_rounds(self.foray_dir, rounds)
            logger.info(f"Round {round_num} complete: {len(round_paths)} experiments")

        self._run_synthesis()

    def _run_experiment(
        self, path: PathInfo, experiment_id: str, findings: list[Finding],
    ) -> ExperimentStatus:
        """Plan -> execute -> assess one experiment."""
        path_findings = [f for f in findings if f.path_id == path.id]

        # --- Plan ---
        state = read_run_state(self.foray_dir)
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
            logger.warning(f"Planner failed for {experiment_id}, retrying simplified")
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
                logger.error(f"Planner failed twice for {experiment_id}")
                return ExperimentStatus.CRASH

        # --- Execute ---
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
            write_crash_stub(self.foray_dir, experiment_id, plan_path, exec_result)

        exp_status = parse_experiment_status(results_path)

        # --- Assess ---
        assessor_template = self._load_agent_prompt("evaluator")
        assessor_ctx = build_evaluator_context(self.foray_dir, experiment_id, path, path_findings)
        assessment_path = self.foray_dir / "experiments" / f"{experiment_id}_eval.json"
        dispatch(
            prompt=(
                f"{assessor_template}\n\n---\n\n{assessor_ctx}\n\n---\n\n"
                f"Write assessment JSON to: {assessment_path}"
            ),
            workdir=self.project_root,
            model=self.config.model,
            max_turns=self.config.max_turns,
            tools=["Read", "Write"],
        )

        assessment = read_evaluation(self.foray_dir, experiment_id)
        finding_summary = assessment.summary if assessment else "(assessment failed)"
        add_finding(self.foray_dir, Finding(
            experiment_id=experiment_id,
            path_id=path.id,
            status=exp_status,
            summary=finding_summary,
            one_liner=finding_summary[:100],
        ))

        if assessment:
            all_findings = read_findings(self.foray_dir)
            new_status = apply_guardrails(assessment, path, all_findings)
            paths = read_paths(self.foray_dir)
            write_paths(self.foray_dir, [
                p.model_copy(update={
                    "status": new_status,
                    "experiment_count": p.experiment_count + 1,
                    "topic_tags": list(set(p.topic_tags + assessment.topic_tags)),
                }) if p.id == path.id else p
                for p in paths
            ])

        # Worktree cleanup
        copy_artifacts(worktree_path, artifacts_dir)
        if not should_preserve_worktree(exp_status):
            cleanup_worktree(self.project_root, worktree_path)
        enforce_worktree_limit(self.foray_dir, self.project_root)

        return exp_status

    def _run_synthesis(self) -> None:
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
        local = self.foray_dir / "agents" / f"{agent_name}.md"
        if local.exists():
            return local.read_text()
        return (Path(__file__).parent / "agents" / f"{agent_name}.md").read_text()

    def _ensure_gitignore(self) -> None:
        gitignore = self.project_root / ".gitignore"
        entry = ".foray/"
        if gitignore.exists():
            content = gitignore.read_text()
            if entry not in content.splitlines():
                gitignore.write_text(content.rstrip() + f"\n{entry}\n")
        else:
            gitignore.write_text(f"{entry}\n")
