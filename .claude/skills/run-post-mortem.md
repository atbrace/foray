---
name: run-post-mortem
description: Exhaustive post-mortem analysis of a completed Foray run. Use when the user says "post-mortem", "analyze the run", "what happened in the run", or points you at a .foray/ directory to analyze.
---

# Foray Run Post-Mortem

Produce a comprehensive post-mortem of a completed Foray run by dispatching parallel analysis agents, then synthesizing findings into a report and filing issues.

## Inputs

Determine the **run directory** (the `.foray/` directory). Either:
- The user specifies a project path (e.g., `~/Developer/printbot`) — look for `.foray/` inside it
- There's a `.foray/` in the current working directory
- Ask if unclear

## Phase 1: Run Overview

Read these files yourself (don't delegate — you need this context):
- `foray.json` — config, experiment count, current round
- `state/paths.json` — all paths with statuses
- `state/rounds.json` — round outcomes (scan for crashes, status distribution)
- `vision.md` — the original question/vision

Produce a quick summary: how many experiments, how many paths, what resolved/blocked/crashed, any obvious anomalies.

## Phase 2: Parallel Deep Analysis

Dispatch **one Explore subagent per path** plus **one for operational metrics**. All run in parallel.

### Per-path agent prompt template:

> Analyze the `{path_id}` exploration path from a Foray run at {foray_dir}
>
> This path had {n} experiments ({experiment_ids}) and status is {status}.
>
> Read all eval files and results files for these experiment IDs in the experiments/ directory.
>
> For each experiment report:
> 1. What was attempted (hypothesis)
> 2. What was found (key result)
> 3. Status and confidence
> 4. What the evaluator recommended next
>
> Then synthesize:
> - The arc of discovery across all experiments
> - Key findings and their evidence quality (independent vs self-evaluated)
> - What was resolved and what gaps remain
> - Whether the final status (resolved/blocked/open) was justified
>
> Keep the report under 600 words (800 for paths with 10+ experiments).

### Operational metrics agent prompt:

> Analyze the operational metrics of a Foray run at {foray_dir}
>
> Read: foray.json, state/rounds.json, state/paths.json, vision.md
>
> Compute and report:
> 1. Timeline: total duration, average round duration, fastest/slowest rounds
> 2. Budget efficiency: experiments used vs budget, hours used vs budget
> 3. Status distribution: count of SUCCESS, PARTIAL, CRASH, FAILED, INFEASIBLE, EXHAUSTED
> 4. Path balance: experiment distribution across paths over time
> 5. Crash analysis: when, clustering, pattern
> 6. Parallelism: experiments per round, was max_concurrent utilized
> 7. Resolution timeline: when did each path resolve/block, was there wasted work after
>
> Keep the report under 500 words.

## Phase 3: Quality Audit

After Phase 2 completes, dispatch **three more Explore agents in parallel**:

### 1. Methodology audit

> For each experiment, read the eval JSON and classify actual methodology:
> - Code artifact (runnable, measurable output)
> - Self-assessment (agent graded its own work)
> - Simulation (reasoning without testing)
> - Mathematical analysis (derivation without empirical validation)
>
> Report: % producing independently verifiable artifacts, % self-assessed, evaluator accuracy on methodology classification, most vs least rigorous experiments.

### 2. Vision alignment audit

> For each experiment, read plan and results. Assess:
> - Vision relevance (HIGH/MEDIUM/LOW)
> - Actionable output? (could be integrated into the pipeline)
> - Redundancy (repeated prior work?)
> - Scope creep (drifted beyond vision needs?)
>
> Report: highest-value experiments, wasted cycles, planner pathologies, recommendations.

### 3. Evaluator accuracy audit

> For each evaluation, assess:
> - Confidence calibration (appropriate given evidence?)
> - Path status recommendation accuracy (should it have closed earlier?)
> - new_questions quality (novel and testable, or derivative?)
> - Self-evaluation detection accuracy
>
> Report: evaluator scorecard, experiments where judgment was wrong, whether evaluator contributed to paths staying open too long, recommendations for prompt tuning.

## Phase 4: Write Post-Mortem

Synthesize all agent reports into a single post-mortem document. Save to `docs/post-mortems/YYYY-MM-DD-{project_name}.md` in the **Foray project** (not the target project's .foray/).

### Document Structure

```markdown
# Foray Run Post-Mortem: {project}

**Date:** YYYY-MM-DD
**Duration:** X hours
**Config:** [key config values]

## Run Summary
[Table: experiments, rounds, paths, status breakdown]

## Vision
[Quote the original vision]

## Path Analysis
### 1. {path_id} ({STATUS}, N experiments)
[Arc, findings, evidence quality, gaps, resolution assessment]
[Repeat for each path]

## Operational Analysis
[Timeline, budget, parallelism, crash patterns]

## Experiment Quality
### Methodology
[% independent vs self-assessed, rigour distribution]
### Vision Alignment
[Highest-value experiments, wasted cycles]
### Evaluator Accuracy
[Scorecard, closure timing, prompt tuning recommendations]

## Foray System Issues
[Bugs, workflow problems, prompt deficiencies discovered]

## Recommendations
[Concrete next steps: prompt changes, issues to file, workflow improvements]
```

## Phase 5: File Issues

Use `/product-manager` to file issues for:
- Any Foray system bugs discovered (crashes, missing features, broken workflows)
- Prompt tuning recommendations that are concrete enough to be actionable
- Workflow improvements identified in the operational analysis

Do NOT file issues for:
- Findings about the target project (that's the project's concern, not Foray's)
- Vague recommendations ("improve the evaluator" — too broad)
- Things that are already tracked in existing issues (check first)
