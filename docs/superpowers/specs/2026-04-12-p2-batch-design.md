# P2 Batch: Four Evaluator & Orchestrator Improvements

## Issues

- **foray-82e** — Auto-resolve paths after consecutive unanimous EXHAUSTED signals
- **foray-57i** — Synthesizer should judge synthetic vs real evidence sufficiency
- **foray-op8** — Interactive path approval before run starts
- **foray-7d8** — Persist per-dispatch timing breakdown and extract token counts

---

## foray-82e: Auto-resolve exhausted paths

**Problem:** A path had 11 consecutive EXHAUSTED signals, none honored, because `apply_guardrails` requires 2+ non-failure experiments for resolution. When exhausted, the evaluator (Opus) has reviewed all evidence and made a judgment call — the 2-experiment minimum is too conservative.

**Change:** In `apply_guardrails`, when `exp_status == EXHAUSTED` and evaluator recommends RESOLVED, relax the experiment minimum from 2 to 1. Confidence and independent-verification checks still apply.

**Files:** `src/foray/orchestrator.py`, `tests/test_orchestrator.py`

---

## foray-57i: Synthesizer evidence sufficiency judgment

**Problem:** Evaluator caps synthetic-only evidence at MEDIUM confidence. But whether MEDIUM is *sufficient* depends on the path's question and domain — a judgment requiring cross-path context that only the synthesizer has.

**Changes:**

1. **Evaluation model:** Add `data_type: str = ""` field (values: `"synthetic"`, `"real"`, `"mixed"`, or empty).

2. **Evaluator prompt:** Classify evidence as synthetic/real/mixed. Keep existing MEDIUM cap for synthetic. Add `data_type` to JSON output spec.

3. **Synthesizer prompt:** Add "Evidence Sufficiency" assessment per resolved path. For each, judge whether the evidence quality is adequate for that path's domain. E.g., "synthetic data is fine for testing pipeline code" vs "synthetic data is insufficient for evaluating real-world OCR accuracy."

**Files:** `src/foray/models.py`, `tests/test_models.py`, `src/foray/agents/evaluator.md`, `src/foray/agents/synthesizer.md`

---

## foray-op8: Interactive path approval

**Problem:** After initializer proposes paths, foray auto-proceeds after a countdown. Users can't review, remove, or edit paths before burning budget.

**Change:** Replace the countdown timer with an explicit confirmation prompt. Show paths, ask user to confirm or edit. `--yes` / `-y` flag skips the prompt for unattended/overnight runs (auto-proceeds immediately, no countdown).

**Interaction flow:**
```
Identified 4 exploration paths:
  [high] path-1: Description...
  [medium] path-2: Description...
  [medium] path-3: Description...
  [low] path-4: Description...

Proceed with these paths? [Y/n/e]
  Y or Enter — start the run
  n — abort (prints edit instructions)
  e — open paths.json in $EDITOR for editing, then re-prompt
```

With `--yes`:
```
Identified 4 exploration paths:
  [high] path-1: Description...
  ...
Starting run (--yes flag, skipping approval)...
```

**Files:** `src/foray/cli.py`, `src/foray/models.py` (add `yes` to `RunConfig`), `tests/test_cli.py` (if exists, otherwise new)

---

## foray-7d8: Timing/token persistence

**Problem:** `_agent_timing` is in-memory only — lost after run. Stream-json output has per-turn token counts that are discarded.

**Changes:**

1. **Token extraction:** Add `parse_stream_json_tokens(stdout: str) -> dict[str, int]` to `dispatcher.py`. Extracts `input_tokens` and `output_tokens` from stream-json `usage` events. Returns `{"input_tokens": N, "output_tokens": N}`.

2. **Timing state file:** `.foray/state/timing.json` — list of timing records, one per dispatch:
   ```json
   [
     {
       "experiment_id": "r1-001",
       "agent_type": "planner",
       "elapsed_seconds": 3.2,
       "input_tokens": 5000,
       "output_tokens": 1200
     }
   ]
   ```

3. **Orchestrator integration:** After each dispatch in `_run_experiment_inner`, append a timing record to `timing.json` via `state.py`. Parse tokens from executor dispatch (which uses stream-json).

4. **Synthesizer context:** `build_synthesizer_context` reads `timing.json` instead of in-memory `_agent_timing`. Remove `_agent_timing` dict from Orchestrator.

**Files:** `src/foray/dispatcher.py`, `src/foray/state.py`, `src/foray/orchestrator.py`, `src/foray/context.py`, `tests/test_dispatcher.py`, `tests/test_state.py`, `tests/test_orchestrator.py`
