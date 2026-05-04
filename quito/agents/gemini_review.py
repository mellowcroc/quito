from __future__ import annotations

import json
import subprocess

from ..models import ReviewComment, Severity, Spec
from ..stages.base import ReviewStage


class GeminiReview(ReviewStage):
    name = "gemini"

    def __init__(self, model: str = "gemini-2.5-flash", use_cli: bool = True):
        self.model = model
        self.use_cli = use_cli
        if not use_cli:
            from google import genai
            self.client = genai.Client()

    def review(
        self,
        code: dict[str, str],
        spec: Spec,
        plan: str = "",
    ) -> list[ReviewComment]:
        code_text = ""
        for path, content in code.items():
            code_text += f"\n--- {path} ---\n{content}\n"

        prompt = f"""Review this code against the spec. Focus on: correctness, security, spec compliance, edge cases, performance.

## Spec
Title: {spec.title}
Description: {spec.description}

Requirements:
{chr(10).join(f'- {r}' for r in spec.requirements)}

Acceptance Criteria:
{chr(10).join(f'- {c}' for c in spec.acceptance_criteria)}

## Implementation Plan
{plan}

## Code
{code_text}

Return a JSON array of findings. Each finding:
{{
  "file": "path/to/file",
  "line": null or line_number,
  "severity": "critical" | "high" | "medium" | "low" | "info",
  "comment": "what's wrong",
  "suggested_fix": "how to fix it"
}}

Return ONLY the JSON array, no markdown fences. If no issues found, return []."""

        output = self._call(prompt)
        return _parse_review(output)

    def _call(self, prompt: str) -> str:
        if self.use_cli:
            return self._call_cli(prompt)
        return self._call_api(prompt)

    def _call_api(self, prompt: str) -> str:
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
        )
        return response.text

    def _call_cli(self, prompt: str) -> str:
        result = subprocess.run(
            ["gemini", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Gemini CLI failed: {result.stderr}")
        return result.stdout


def _parse_review(output: str) -> list[ReviewComment]:
    if not output:
        return []

    text = output.strip()
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
            end = bracket_start
            for i in range(bracket_start, len(text)):
                if text[i] == "[":
                    bracket_count += 1
                elif text[i] == "]":
                    bracket_count -= 1
                    if bracket_count == 0:
                        end = i + 1
                        break
            try:
                items = json.loads(text[bracket_start:end])
            except json.JSONDecodeError:
                return []
        else:
            return []

    if not isinstance(items, list):
        return []

    comments = []
    for item in items:
        try:
            severity = Severity(item.get("severity", "medium").lower())
        except ValueError:
            severity = Severity.MEDIUM
        comments.append(ReviewComment(
            file=item.get("file", "unknown"),
            line=item.get("line"),
            severity=severity,
            comment=item.get("comment", ""),
            suggested_fix=item.get("suggested_fix", ""),
        ))
    return comments
