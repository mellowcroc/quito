from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ..models import ReviewComment, Spec, VisualFinding
from ..store import RunStore


class Stage(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def run(self, ctx: PipelineContext) -> PipelineContext: ...


class PipelineContext:
    def __init__(self, spec: Spec, store: RunStore, config: Any):
        self.spec = spec
        self.store = store
        self.config = config
        self.iteration: int = 0
        self.code: dict[str, str] = {}
        self.plan: str = ""
        self.review_comments: list[ReviewComment] = []
        self.visual_findings: list[VisualFinding] = []
        self.feedback: list[ReviewComment] = []
        self.extra: dict[str, Any] = {}


class CodegenStage(Stage):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def generate(
        self,
        spec: Spec,
        feedback: list[ReviewComment] | None,
        existing_code: dict[str, str] | None,
    ) -> tuple[str, dict[str, str]]: ...

    @abstractmethod
    def apply_review(
        self,
        code: dict[str, str],
        comments: list[ReviewComment],
        spec: Spec,
    ) -> tuple[dict[str, str], list[dict]]: ...

    def run(self, ctx: PipelineContext) -> PipelineContext:
        feedback = ctx.feedback if ctx.iteration > 1 else None
        existing = ctx.code if ctx.iteration > 1 else None
        ctx.plan, ctx.code = self.generate(ctx.spec, feedback, existing)
        return ctx


class ReviewStage(Stage):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def review(self, code: dict[str, str], spec: Spec, plan: str) -> list[ReviewComment]: ...

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.review_comments = self.review(ctx.code, ctx.spec, ctx.plan)
        return ctx


class VisualQAStage(Stage):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def review_screenshots(self, screenshot_paths: list, spec: Spec) -> list[VisualFinding]: ...

    @abstractmethod
    def review_video(self, video_path, spec: Spec) -> list[VisualFinding]: ...

    def run(self, ctx: PipelineContext) -> PipelineContext:
        return ctx


class FixStage(Stage):
    """Applies fixes to actual project files based on review comments."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def fix(
        self,
        project_dir: Path,
        comments: list[ReviewComment],
        spec: Spec,
    ) -> list[dict]:
        """Apply fixes to files in project_dir. Returns a list of actions taken."""
        ...

    def run(self, ctx: PipelineContext) -> PipelineContext:
        project_dir = Path(ctx.config.project_dir)
        actions = self.fix(project_dir, ctx.review_comments, ctx.spec)
        ctx.extra["fix_actions"] = actions
        return ctx


class VerifyStage(Stage):
    """Runs project verification commands (tests, typecheck, lint)."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def verify(self, project_dir: Path) -> list[dict]:
        """Run verification. Returns list of {command, passed, output}."""
        ...

    def run(self, ctx: PipelineContext) -> PipelineContext:
        project_dir = Path(ctx.config.project_dir)
        results = self.verify(project_dir)
        ctx.extra["verify_results"] = results
        return ctx
