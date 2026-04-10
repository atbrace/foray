from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


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


class PathInfo(BaseModel):
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


class Evaluation(BaseModel):
    experiment_id: str
    path_id: str
    outcome: str
    path_status: PathStatus
    confidence: Confidence
    topic_tags: list[str] = Field(default_factory=list)
    summary: str
    new_questions: list[str] = Field(default_factory=list)
    evidence_for: dict[str, str] = Field(default_factory=dict)
    evidence_against: dict[str, str] = Field(default_factory=dict)
    blocker_description: str = ""


class RunConfig(BaseModel):
    vision_path: str
    hours: float = 8.0
    max_experiments: int = 50
    model: str = "claude-sonnet-4-20250514"
    max_turns: int = 30
    output_dir: str = ".foray/"
    allow_tools: list[str] = Field(default_factory=list)
    deny_tools: list[str] = Field(default_factory=list)


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
