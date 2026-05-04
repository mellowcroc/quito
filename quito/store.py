from __future__ import annotations

import json
import uuid
from pathlib import Path

from .models import (
    BugbashFinding,
    BugbashPersona,
    GateResult,
    IterationResult,
    ReviewComment,
    Spec,
    VisualFinding,
)


class RunStore:
    def __init__(self, base_dir: Path, run_id: str | None = None):
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.root = base_dir / f"run-{self.run_id}"
        self.root.mkdir(parents=True, exist_ok=True)

    def iteration_dir(self, n: int) -> Path:
        d = self.root / f"iteration-{n}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def bugbash_dir(self) -> Path:
        d = self.root / "bugbash"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -- Spec --

    def save_spec(self, spec: Spec) -> Path:
        p = self.root / "spec.json"
        p.write_text(spec.model_dump_json(indent=2))
        raw_p = self.root / "spec.md"
        raw_p.write_text(spec.raw)
        return p

    def load_spec(self) -> Spec:
        p = self.root / "spec.json"
        return Spec.model_validate_json(p.read_text())

    # -- Plan --

    def save_plan(self, iteration: int, plan: str) -> Path:
        p = self.iteration_dir(iteration) / "plan.md"
        p.write_text(plan)
        return p

    def load_plan(self, iteration: int) -> str:
        return (self.iteration_dir(iteration) / "plan.md").read_text()

    # -- Code --

    def code_dir(self, iteration: int) -> Path:
        d = self.iteration_dir(iteration) / "code"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_code_file(self, iteration: int, relative_path: str, content: str) -> Path:
        p = self.code_dir(iteration) / relative_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def load_code_files(self, iteration: int) -> dict[str, str]:
        code_d = self.code_dir(iteration)
        if not code_d.exists():
            return {}
        files = {}
        for f in code_d.rglob("*"):
            if f.is_file():
                files[str(f.relative_to(code_d))] = f.read_text()
        return files

    # -- Review --

    def save_review(self, iteration: int, comments: list[ReviewComment]) -> Path:
        p = self.iteration_dir(iteration) / "review.json"
        p.write_text(json.dumps([c.model_dump() for c in comments], indent=2))
        return p

    def load_review(self, iteration: int) -> list[ReviewComment]:
        p = self.iteration_dir(iteration) / "review.json"
        if not p.exists():
            return []
        return [ReviewComment.model_validate(c) for c in json.loads(p.read_text())]

    # -- Screenshots --

    def screenshots_dir(self, iteration: int) -> Path:
        d = self.iteration_dir(iteration) / "screenshots"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def video_path(self, iteration: int) -> Path:
        return self.iteration_dir(iteration) / "recording.webm"

    # -- Visual feedback --

    def save_visual_feedback(self, iteration: int, findings: list[VisualFinding]) -> Path:
        p = self.iteration_dir(iteration) / "visual_feedback.json"
        p.write_text(json.dumps([f.model_dump() for f in findings], indent=2))
        return p

    def load_visual_feedback(self, iteration: int) -> list[VisualFinding]:
        p = self.iteration_dir(iteration) / "visual_feedback.json"
        if not p.exists():
            return []
        return [VisualFinding.model_validate(f) for f in json.loads(p.read_text())]

    # -- Gate --

    def save_gate(self, iteration: int, gate: GateResult) -> Path:
        p = self.iteration_dir(iteration) / "gate_decision.json"
        p.write_text(gate.model_dump_json(indent=2))
        return p

    def load_gate(self, iteration: int) -> GateResult | None:
        p = self.iteration_dir(iteration) / "gate_decision.json"
        if not p.exists():
            return None
        return GateResult.model_validate_json(p.read_text())

    # -- Update response --

    def save_update_response(self, iteration: int, response: dict) -> Path:
        p = self.iteration_dir(iteration) / "update_response.json"
        p.write_text(json.dumps(response, indent=2))
        return p

    # -- Iteration result --

    def save_iteration_result(self, result: IterationResult) -> Path:
        p = self.iteration_dir(result.iteration) / "result.json"
        p.write_text(result.model_dump_json(indent=2))
        return p

    # -- Bugbash --

    def save_personas(self, personas: list[BugbashPersona]) -> Path:
        d = self.bugbash_dir() / "personas"
        d.mkdir(parents=True, exist_ok=True)
        for persona in personas:
            p = d / f"{persona.id}.json"
            p.write_text(persona.model_dump_json(indent=2))
        return d

    def load_personas(self) -> list[BugbashPersona]:
        d = self.bugbash_dir() / "personas"
        if not d.exists():
            return []
        return [BugbashPersona.model_validate_json(f.read_text()) for f in sorted(d.glob("*.json"))]

    def save_bugbash_finding(self, finding: BugbashFinding) -> Path:
        d = self.bugbash_dir() / "findings"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{finding.persona_id}.json"
        p.write_text(finding.model_dump_json(indent=2))
        return p

    def load_bugbash_findings(self) -> list[BugbashFinding]:
        d = self.bugbash_dir() / "findings"
        if not d.exists():
            return []
        return [BugbashFinding.model_validate_json(f.read_text()) for f in sorted(d.glob("*.json"))]

    def save_bugbash_report(self, report: str) -> Path:
        p = self.bugbash_dir() / "report.md"
        p.write_text(report)
        return p

    def save_clustered_findings(self, clustered: list[dict]) -> Path:
        p = self.bugbash_dir() / "clustered.json"
        p.write_text(json.dumps(clustered, indent=2))
        return p

    # -- Summary --

    def save_summary(self, summary: str) -> Path:
        p = self.root / "summary.md"
        p.write_text(summary)
        return p
