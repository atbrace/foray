from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from foray.models import PathStatus, RunConfig
from foray.orchestrator import Orchestrator
from foray.state import read_paths, read_run_state


@click.group()
def main():
    """Foray - autonomous exploration tool."""
    pass


@main.command()
@click.option("--vision", type=click.Path(exists=True), help="Path to vision document")
@click.option("--question", type=str, help="Inline exploration question")
@click.option("--hours", type=float, default=8.0, show_default=True, help="Time budget")
@click.option("--max-experiments", type=int, default=50, show_default=True, help="Experiment cap")
@click.option("--model", type=str, default="claude-sonnet-4-6", show_default=True)
@click.option("--evaluator-model", type=str, default="claude-opus-4-6", show_default=True, help="Model for evaluator agent")
@click.option("--max-turns", type=int, default=30, show_default=True)
@click.option("--max-concurrent", type=int, default=3, show_default=True, help="Max parallel experiments per round")
@click.option("--output", type=str, default=".foray/", show_default=True, help="Output directory")
@click.option("--allow", multiple=True, help="Additional tools to enable")
@click.option("--deny", multiple=True, help="Tools to disable")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip path approval prompt")
def run(vision, question, hours, max_experiments, model, evaluator_model, max_turns, max_concurrent, output, allow, deny, yes):
    """Start an exploration run."""
    project_root = Path.cwd()

    if not vision and not question:
        question = click.prompt("What do you want to explore?")

    if question and not vision:
        vision_dir = project_root / output
        vision_dir.mkdir(parents=True, exist_ok=True)
        vision_path = vision_dir / "vision.md"
        vision_path.write_text(f"# Exploration\n\n## Where I'm Stuck\n\n{question}\n")
        vision = str(vision_path)

    config = RunConfig(
        vision_path=vision,
        hours=hours,
        max_experiments=max_experiments,
        model=model,
        evaluator_model=evaluator_model,
        max_turns=max_turns,
        output_dir=output,
        allow_tools=list(allow),
        deny_tools=list(deny),
        max_concurrent=max_concurrent,
        yes=yes,
    )

    orchestrator = Orchestrator(project_root, config)
    foray_dir = orchestrator.init()

    paths = read_paths(foray_dir)
    click.echo(f"\nIdentified {len(paths)} exploration paths:")
    for p in paths:
        click.echo(f"  [{p.priority}] {p.id}: {p.description}")

    if yes:
        click.echo("\nStarting run (--yes flag, skipping approval)...")
    else:
        click.echo()
        while True:
            choice = click.prompt(
                "Proceed with these paths? [Y/n/e]",
                default="y", show_default=False,
            ).strip().lower()
            if choice in ("y", "yes", ""):
                break
            elif choice in ("n", "no"):
                click.echo("Aborted. Edit paths at .foray/state/paths.json and re-run.")
                sys.exit(0)
            elif choice in ("e", "edit"):
                paths_file = foray_dir / "state" / "paths.json"
                click.edit(filename=str(paths_file))
                paths = read_paths(foray_dir)
                click.echo(f"\nUpdated — {len(paths)} exploration paths:")
                for p in paths:
                    click.echo(f"  [{p.priority}] {p.id}: {p.description}")
                click.echo()
            else:
                click.echo("Please enter Y, n, or e.")

    orchestrator.run()
    synthesis_path = foray_dir / "synthesis.md"
    if synthesis_path.exists():
        click.echo(f"\nExploration complete. Report at: {synthesis_path}")
    else:
        click.echo("\nExploration complete. Warning: synthesis report was not generated.")


@main.command()
def report():
    """Print synthesis report to stdout."""
    report_path = Path.cwd() / ".foray" / "synthesis.md"
    if not report_path.exists():
        click.echo("No report found. Run 'foray run' first.", err=True)
        sys.exit(1)
    click.echo(report_path.read_text())


@main.command()
def status():
    """Show current run status."""
    foray_dir = Path.cwd() / ".foray"
    if not foray_dir.exists():
        click.echo("No Foray run found.", err=True)
        sys.exit(1)
    state = read_run_state(foray_dir)
    paths = read_paths(foray_dir)
    open_n = sum(1 for p in paths if p.status == PathStatus.OPEN)
    resolved_n = sum(1 for p in paths if p.status == PathStatus.RESOLVED)
    click.echo(f"Experiments: {state.experiment_count}")
    click.echo(f"Round: {state.current_round}")
    click.echo(f"Paths: {open_n} open, {resolved_n} resolved, {len(paths)} total")
    click.echo(f"Started: {state.start_time}")


@main.command()
def resume():
    """Resume a stopped or crashed run."""
    foray_dir = Path.cwd() / ".foray"
    if not foray_dir.exists():
        click.echo("No Foray run found to resume.", err=True)
        sys.exit(1)
    state = read_run_state(foray_dir)
    orchestrator = Orchestrator(Path.cwd(), state.config)
    orchestrator.foray_dir = foray_dir
    orchestrator.run()
    synthesis_path = foray_dir / "synthesis.md"
    if synthesis_path.exists():
        click.echo(f"\nExploration complete. Report at: {synthesis_path}")
    else:
        click.echo("\nExploration complete. Warning: synthesis report was not generated.")
