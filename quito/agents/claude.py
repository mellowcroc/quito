from __future__ import annotations

import json
import subprocess

import anthropic

from ..models import ReviewComment, Spec
from ..stages.base import CodegenStage


class ClaudeCodegen(CodegenStage):
    name = "claude"

    def __init__(self, model: str = "claude-opus-4-7", use_cli: bool = False):
        self.model = model
        self.use_cli = use_cli
        if not use_cli:
            self.client = anthropic.Anthropic()

    def generate(
        self,
        spec: Spec,
        feedback: list[ReviewComment] | None = None,
        existing_code: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, str]]:
        feedback_text = ""
        if feedback:
            feedback_text = "Previous review feedback to address:\n"
            for c in feedback:
                feedback_text += f"- [{c.severity.value}] {c.file}:{c.line}: {c.comment}\n"
                if c.suggested_fix:
                    feedback_text += f"  Suggested fix: {c.suggested_fix}\n"

        existing_text = ""
        if existing_code:
            existing_text = "Existing code to update:\n"
            for path, content in existing_code.items():
                existing_text += f"\n--- {path} ---\n{content}\n"

        prompt = f"""You are implementing a web application based on the following spec.

## Spec
Title: {spec.title}
Description: {spec.description}

Requirements:
{chr(10).join(f"- {r}" for r in spec.requirements)}

Acceptance Criteria:
{chr(10).join(f"- {c}" for c in spec.acceptance_criteria)}

UI Description:
{spec.ui_description}

{feedback_text}
{existing_text}

First, write a brief implementation plan. Then provide the full source files.

Return your response as JSON with this structure:
{{
  "plan": "your implementation plan as markdown",
  "files": {{
    "relative/path/to/file.ext": "file content",
    ...
  }}
}}

Return ONLY the JSON, no markdown fences."""

        response_text = self._call(prompt)
        parsed = _extract_json(response_text)
        plan = parsed.get("plan", "")
        files = parsed.get("files", {})
        return plan, files

    def apply_review(
        self,
        code: dict[str, str],
        comments: list[ReviewComment],
        spec: Spec,
    ) -> tuple[dict[str, str], list[dict]]:
        comments_text = ""
        for c in comments:
            comments_text += f"- [{c.severity.value}] {c.file}:{c.line}: {c.comment}\n"
            if c.suggested_fix:
                comments_text += f"  Suggested fix: {c.suggested_fix}\n"

        code_text = ""
        for path, content in code.items():
            code_text += f"\n--- {path} ---\n{content}\n"

        prompt = f"""You are updating code based on review comments.

## Original Spec
{spec.title}: {spec.description}

## Current Code
{code_text}

## Review Comments
{comments_text}

For each comment, either apply the fix or explain why you disagree.

Return JSON:
{{
  "files": {{
    "relative/path/to/file.ext": "updated file content",
    ...
  }},
  "responses": [
    {{"comment": "original comment summary", "action": "fixed" or "declined", "explanation": "..."}}
  ]
}}

Return ONLY the JSON, no markdown fences."""

        response_text = self._call(prompt)
        parsed = _extract_json(response_text)
        files = parsed.get("files", code)
        responses = parsed.get("responses", [])
        return files, responses

    def _call(self, prompt: str) -> str:
        if self.use_cli:
            return self._call_cli(prompt)
        return self._call_api(prompt)

    def _call_api(self, prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=16000,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    def _call_cli(self, prompt: str) -> str:
        result = subprocess.run(
            ["claude", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Claude CLI failed: {result.stderr}")
        return result.stdout


def _extract_json(text: str) -> dict:
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
        return json.loads(text)
    except json.JSONDecodeError:
        json_match = text.find("{")
        if json_match >= 0:
            brace_count = 0
            end = json_match
            for i in range(json_match, len(text)):
                if text[i] == "{":
                    brace_count += 1
                elif text[i] == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        end = i + 1
                        break
            try:
                return json.loads(text[json_match:end])
            except json.JSONDecodeError:
                pass
        return {"plan": "", "files": {}}
