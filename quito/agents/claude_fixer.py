from __future__ import annotations

import json
import subprocess
from pathlib import Path

import anthropic

from ..models import ReviewComment, Severity, Spec, VisualFinding
from ..stages.base import FixStage

UI_EXTENSIONS = {".tsx", ".jsx", ".ts", ".js", ".css", ".html", ".svelte", ".vue"}
CONFIG_FILENAMES = {
    "package.json", "tsconfig.json", "tsconfig.node.json",
    "next.config.js", "next.config.mjs", "next.config.ts",
    "vite.config.ts", "vite.config.js",
    "webpack.config.js", "webpack.config.ts",
    "babel.config.js", "babel.config.cjs", ".babelrc",
    "turbo.json", "pnpm-workspace.yaml",
}


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

    def fix_visual(
        self,
        project_dir: Path,
        findings: list[VisualFinding],
        spec: Spec,
    ) -> list[dict]:
        actionable = [f for f in findings if f.severity in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM)]
        if not actionable:
            return []

        ui_files: dict[str, str] = {}
        for p in sorted(project_dir.rglob("*")):
            if not p.is_file() or p.suffix not in UI_EXTENSIONS:
                continue
            rel = str(p.relative_to(project_dir))
            if "node_modules" in rel or ".next" in rel or "dist" in rel:
                continue
            try:
                ui_files[rel] = p.read_text(errors="replace")
            except Exception:
                continue

        if not ui_files:
            return []

        findings_text = ""
        for f in actionable:
            findings_text += f"- [{f.severity.value}] {f.issue}\n"
            if f.suggestion:
                findings_text += f"  Suggestion: {f.suggestion}\n"

        files_text = ""
        for path, content in ui_files.items():
            files_text += f"\n### {path}\n```\n{content}\n```\n"

        prompt = f"""Fix the visual/UI issues found by QA review. The project is: {spec.title}.

## Visual issues to fix

{findings_text}

## UI source files

{files_text}

For each issue, identify which file(s) need changes and apply the fix.

Return JSON:
{{
  "files": {{
    "path/to/file.tsx": "complete updated file content",
    "path/to/other.css": "complete updated file content"
  }},
  "actions": [
    {{"issue": "issue summary", "action": "fixed" or "declined", "files": ["path/to/file.tsx"], "explanation": "what you did"}}
  ]
}}

Only include files that actually changed. Return ONLY the JSON, no markdown fences."""

        response_text = self._call(prompt)
        parsed = _extract_json(response_text)

        changed_files = parsed.get("files", {})
        actions = parsed.get("actions", [])

        for file_path, new_content in changed_files.items():
            full_path = project_dir / file_path
            if full_path.exists() and new_content:
                full_path.write_text(new_content)

        return actions

    def fix_build(
        self,
        project_dir: Path,
        verify_results: list[dict],
        spec: Spec,
    ) -> list[dict]:
        failures = [v for v in verify_results if not v["passed"]]
        if not failures:
            return []

        errors_text = ""
        for v in failures:
            errors_text += f"### `{v['command']}`\n```\n{v['output']}\n```\n\n"

        config_files: dict[str, str] = {}
        for p in sorted(project_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = str(p.relative_to(project_dir))
            if "node_modules" in rel or ".next" in rel or "dist" in rel or "build" in rel:
                continue
            if p.name in CONFIG_FILENAMES:
                try:
                    config_files[rel] = p.read_text(errors="replace")
                except Exception:
                    continue

        files_text = ""
        for path, content in config_files.items():
            files_text += f"\n### {path}\n```\n{content}\n```\n"

        prompt = f"""Fix the build/config errors below. The project is: {spec.title}.
This is a monorepo — workspace dependencies (e.g. "workspace:*") resolve via symlinks in node_modules.
Do NOT change bare package imports to relative paths if a workspace dependency exists for that package.
Focus on fixing the actual root cause: missing tsconfig paths, incorrect main/exports fields,
missing build steps, wrong module resolution settings, etc.

## Build errors

{errors_text}

## Config files

{files_text}

Fix whatever is causing the build to fail. Return JSON:
{{
  "files": {{
    "path/to/tsconfig.json": "complete updated file content",
    "path/to/package.json": "complete updated file content"
  }},
  "actions": [
    {{"issue": "issue summary", "action": "fixed" or "declined", "files": ["path/to/file"], "explanation": "what you did"}}
  ]
}}

Only include files that actually changed. Return ONLY the JSON, no markdown fences."""

        response_text = self._call(prompt)
        parsed = _extract_json(response_text)

        changed_files = parsed.get("files", {})
        actions = parsed.get("actions", [])

        for file_path, new_content in changed_files.items():
            full_path = project_dir / file_path
            if full_path.exists() and new_content:
                full_path.write_text(new_content)

        return actions

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
