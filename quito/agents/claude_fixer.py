from __future__ import annotations

import json
import subprocess
from pathlib import Path

import anthropic

from ..models import ReviewComment, Severity, Spec
from ..stages.base import FixStage


class ClaudeFixer(FixStage):
    name = "claude"

    def __init__(self, model: str = "claude-opus-4-7", use_cli: bool = True):
        self.model = model
        self.use_cli = use_cli
        if not use_cli:
            self.client = anthropic.Anthropic()

    def fix(
        self,
        project_dir: Path,
        comments: list[ReviewComment],
        spec: Spec,
    ) -> list[dict]:
        critical_high = [c for c in comments if c.severity in (Severity.CRITICAL, Severity.HIGH)]
        medium = [c for c in comments if c.severity == Severity.MEDIUM]
        to_fix = critical_high + medium
        if not to_fix:
            return []

        grouped: dict[str, list[ReviewComment]] = {}
        for c in to_fix:
            grouped.setdefault(c.file, []).append(c)

        all_actions = []
        for file_path, file_comments in grouped.items():
            full_path = project_dir / file_path
            if not full_path.exists():
                all_actions.append(
                    {
                        "file": file_path,
                        "action": "skipped",
                        "reason": "file not found",
                    }
                )
                continue

            original = full_path.read_text(errors="replace")
            actions = self._fix_file(project_dir, file_path, original, file_comments, spec)
            all_actions.extend(actions)

        return all_actions

    def _fix_file(
        self,
        project_dir: Path,
        file_path: str,
        original_content: str,
        comments: list[ReviewComment],
        spec: Spec,
    ) -> list[dict]:
        comments_text = ""
        for c in comments:
            comments_text += f"- [{c.severity.value}] line {c.line}: {c.comment}\n"
            if c.suggested_fix:
                comments_text += f"  Suggested fix: {c.suggested_fix}\n"

        prompt = f"""Fix the issues listed below in this file. The file is part of a project: {spec.title}.

## File: {file_path}

```
{original_content}
```

## Issues to fix

{comments_text}

For each issue, apply the fix directly. If you cannot fix an issue without breaking other things, explain why you're declining it.

Return JSON:
{{
  "fixed_content": "the complete updated file content",
  "actions": [
    {{"comment": "issue summary", "action": "fixed" or "declined", "explanation": "what you did or why you declined"}}
  ]
}}

Return ONLY the JSON, no markdown fences."""

        response_text = self._call(prompt)
        parsed = _extract_json(response_text)

        fixed_content = parsed.get("fixed_content")
        actions = parsed.get("actions", [])

        if fixed_content and fixed_content != original_content:
            full_path = project_dir / file_path
            full_path.write_text(fixed_content)
            for a in actions:
                a["file"] = file_path

        return actions

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
        return {}
