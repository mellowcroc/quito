from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..models import Severity, Spec, VisualFinding
from ..stages.base import VisualQAStage


class GeminiVisualQA(VisualQAStage):
    name = "gemini"

    def __init__(self, model: str = "gemini-2.5-flash", use_cli: bool = False):
        self.model = model
        self.use_cli = use_cli
        if not use_cli:
            from google import genai

            self.client = genai.Client()

    def review_screenshots(
        self,
        screenshot_paths: list[Path],
        spec: Spec,
    ) -> list[VisualFinding]:
        if not screenshot_paths:
            return []

        prompt = f"""You are reviewing UI screenshots against a spec.

## Spec
Title: {spec.title}
Description: {spec.description}
UI Description: {spec.ui_description}

Acceptance Criteria:
{chr(10).join(f"- {c}" for c in spec.acceptance_criteria)}

The following screenshots show the current implementation in order of the user flow.
Identify every UI/UX issue: layout problems, missing elements, wrong text, accessibility issues, responsive issues, visual bugs.

Return a JSON array:
[
  {{
    "screenshot": "filename",
    "issue": "description of the issue",
    "severity": "critical" | "high" | "medium" | "low",
    "suggestion": "how to fix it"
  }}
]

Return ONLY the JSON array. If no issues, return []."""

        if self.use_cli:
            response_text = self._call_cli_with_files(prompt, screenshot_paths)
        else:
            response_text = self._call_api_screenshots(prompt, screenshot_paths)

        return _parse_visual_findings(response_text, source="screenshot")

    def review_video(
        self,
        video_path: Path,
        spec: Spec,
    ) -> list[VisualFinding]:
        if not video_path.exists():
            return []

        prompt = f"""Watch this video of a user interacting with a web application.

## Spec
Title: {spec.title}
Description: {spec.description}
UI Description: {spec.ui_description}

Identify interaction issues: broken transitions, unresponsive elements, layout shifts,
flickers, confusing navigation, missing loading states, accessibility problems.

Return a JSON array:
[
  {{
    "issue": "description of the issue",
    "severity": "critical" | "high" | "medium" | "low",
    "suggestion": "how to fix it"
  }}
]

Return ONLY the JSON array. If no issues, return []."""

        if self.use_cli:
            response_text = self._call_cli_with_files(prompt, [video_path])
        else:
            response_text = self._call_api_video(prompt, video_path)

        return _parse_visual_findings(response_text, source="video")

    def _call_api_screenshots(self, prompt: str, paths: list[Path]) -> str:
        uploads = []
        for p in paths:
            uploads.append(self.client.files.upload(file=p))

        response = self.client.models.generate_content(
            model=self.model,
            contents=[prompt] + uploads,
        )

        for uploaded in uploads:
            try:
                self.client.files.delete(name=uploaded.name)
            except Exception:
                pass

        return response.text

    def _call_api_video(self, prompt: str, video_path: Path) -> str:
        uploaded = self.client.files.upload(file=video_path)
        response = self.client.models.generate_content(
            model=self.model,
            contents=[prompt, uploaded],
        )
        try:
            self.client.files.delete(name=uploaded.name)
        except Exception:
            pass
        return response.text

    def _call_cli_with_files(self, prompt: str, file_paths: list[Path]) -> str:
        file_args = []
        for p in file_paths:
            file_args.extend(["-f", str(p)])
        result = subprocess.run(
            ["gemini", "-p", prompt] + file_args,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Gemini CLI failed: {result.stderr}")
        return result.stdout


def _parse_visual_findings(text: str, source: str) -> list[VisualFinding]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1
        end = len(lines)
        for i in range(1, len(lines)):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[start:end])

    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        bracket_start = text.find("[")
        if bracket_start >= 0:
            bracket_count = 0
            end_pos = bracket_start
            for i in range(bracket_start, len(text)):
                if text[i] == "[":
                    bracket_count += 1
                elif text[i] == "]":
                    bracket_count -= 1
                    if bracket_count == 0:
                        end_pos = i + 1
                        break
            try:
                items = json.loads(text[bracket_start:end_pos])
            except json.JSONDecodeError:
                return []
        else:
            return []

    if not isinstance(items, list):
        return []

    findings = []
    for item in items:
        try:
            severity = Severity(item.get("severity", "medium").lower())
        except ValueError:
            severity = Severity.MEDIUM
        findings.append(
            VisualFinding(
                screenshot=item.get("screenshot", ""),
                source=source,
                issue=item.get("issue", ""),
                severity=severity,
                suggestion=item.get("suggestion", ""),
            )
        )
    return findings
