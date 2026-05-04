from __future__ import annotations

import enum
from pathlib import Path

from pydantic import BaseModel, Field


class Severity(enum.StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class UserFlow(BaseModel):
    name: str
    steps: list[FlowStep]


class FlowStep(BaseModel):
    id: str
    action: str  # "navigate", "click", "type", "wait", "assert"
    selector: str | None = None
    value: str | None = None
    description: str = ""


class Spec(BaseModel):
    title: str
    description: str
    requirements: list[str]
    acceptance_criteria: list[str]
    user_flows: list[UserFlow] = Field(default_factory=list)
    ui_description: str = ""
    raw: str = ""


class ReviewComment(BaseModel):
    file: str
    line: int | None = None
    severity: Severity
    comment: str
    suggested_fix: str = ""


class VisualFinding(BaseModel):
    screenshot: str = ""
    source: str = "screenshot"  # "screenshot" or "video"
    issue: str
    severity: Severity
    suggestion: str = ""


class GateDecision(enum.StrEnum):
    LOOP_BACK = "loop_back"
    PROCEED_TO_BUGBASH = "proceed_to_bugbash"
    HALT = "halt"


class GateResult(BaseModel):
    decision: GateDecision
    reason: str
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    iteration: int = 0


class BugbashPersona(BaseModel):
    id: str
    angle: str  # "security", "edge_case", "accessibility", etc.
    strategy: str
    viewport: dict = Field(default_factory=lambda: {"width": 1280, "height": 720})


class BugbashFinding(BaseModel):
    persona_id: str
    issue: str
    severity: Severity
    reproduction_steps: list[str]
    screenshot: str = ""
    category: str = ""


class IterationResult(BaseModel):
    iteration: int
    review_comments: list[ReviewComment] = Field(default_factory=list)
    visual_findings: list[VisualFinding] = Field(default_factory=list)
    gate: GateResult | None = None


class RunConfig(BaseModel):
    spec_path: Path | None = None
    project_dir: Path | None = None
    output_dir: Path | None = None
    max_iterations: int = 5
    bugbash_agents: int = 100
    bugbash_concurrency: int = 20
    app_command: str | None = None  # command to start the dev server
    app_url: str = "http://localhost:3000"
    claude_model: str = "claude-opus-4-7"
    gemini_model: str = "gemini-2.5-flash"
    reviewers: list[str] = Field(default_factory=lambda: ["codex"])
    use_cli: bool = False
