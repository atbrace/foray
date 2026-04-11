from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ExperimentStatus(StrEnum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    INFEASIBLE = "INFEASIBLE"
    CRASH = "CRASH"


class PathStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"


class Priority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class _AgentOutput(BaseModel):
    """Base for models deserialized from agent-written JSON.

    LLM agents often emit ``null`` for fields they consider absent, even when
    the Pydantic schema expects a concrete default (e.g. ``str = ""``).  This
    pre-validator coerces ``null`` to the field's default so parsing doesn't
    blow up on otherwise-valid agent output.
    """

    @model_validator(mode="before")
    @classmethod
    def _coerce_nulls(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for name, field in cls.model_fields.items():
            if name in data and data[name] is None and field.default is not None:
                data[name] = field.default
        return data


class PathInfo(_AgentOutput):
    id: str
    description: str
    priority: Priority
    hypothesis: str
    status: PathStatus = PathStatus.OPEN
    experiment_count: int = 0
    topic_tags: list[str] = Field(default_factory=list)
    blocker_description: str = ""


class RoundOutcome(BaseModel):
    path_id: str
    experiment_id: str
    status: ExperimentStatus
    path_status_after: PathStatus


class Round(BaseModel):
    round_number: int
    paths: list[str]
    outcomes: list[RoundOutcome] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime | None = None


class Finding(BaseModel):
    experiment_id: str
    path_id: str
    status: ExperimentStatus
    summary: str
    one_liner: str
    planner_brief: str = ""


class Evaluation(_AgentOutput):
    experiment_id: str
    path_id: str
    outcome: str
    path_status: PathStatus
    confidence: Confidence
    topic_tags: list[str] = Field(default_factory=list)
    summary: str
    planner_brief: str = ""
    new_questions: list[str] = Field(default_factory=list)
    evidence_for: dict[str, str] = Field(default_factory=dict)
    evidence_against: dict[str, str] = Field(default_factory=dict)
    blocker_description: str = ""
    methodology: str = ""

    @model_validator(mode="after")
    def _cap_self_eval_confidence(self) -> Evaluation:
        if self.methodology == "self-evaluated" and self.confidence == Confidence.HIGH:
            self.confidence = Confidence.MEDIUM
        return self


class RunConfig(BaseModel):
    vision_path: str
    hours: float = 8.0
    max_experiments: int = 50
    model: str = "claude-sonnet-4-6"
    evaluator_model: str = "claude-opus-4-6"
    max_turns: int = 30
    output_dir: str = ".foray/"
    allow_tools: list[str] = Field(default_factory=list)
    deny_tools: list[str] = Field(default_factory=list)
    max_concurrent: int = 3


class RunState(BaseModel):
    start_time: datetime
    config: RunConfig
    experiment_count: int = 0
    current_round: int = 0
    current_path_index: int = 0
    last_completed_experiment: str | None = None


class DispatchResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    results_file_path: str | None = None


class ExperimentResult(BaseModel):
    """Carries outputs from one experiment for deferred state mutation."""
    experiment_id: str
    path_id: str
    exp_status: ExperimentStatus
    finding: Finding
    assessment: Evaluation | None = None
