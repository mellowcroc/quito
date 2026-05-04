from __future__ import annotations

import subprocess
from pathlib import Path

from ..models import Spec
from ..stages.spec_parse import parse_spec

SCAN_PATTERNS = [
    "README.md",
    "README.rst",
    "README.txt",
    "ARCHITECTURE.md",
    "DESIGN.md",
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "docker-compose.yml",
    "Dockerfile",
]

SOURCE_EXTENSIONS = {
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".py",
    ".rs",
    ".go",
    ".vue",
    ".svelte",
    ".html",
    ".css",
    ".scss",
}

MAX_FILE_SIZE = 50_000
MAX_TOTAL_CHARS = 200_000


def scan_codebase(project_dir: Path) -> str:
    sections = []

    for pattern in SCAN_PATTERNS:
        for p in project_dir.rglob(pattern):
            if _should_skip(p):
                continue
            try:
                content = p.read_text(errors="replace")[:MAX_FILE_SIZE]
                rel = p.relative_to(project_dir)
                sections.append(f"--- {rel} ---\n{content}")
            except Exception:
                continue

    source_files = []
    for p in sorted(project_dir.rglob("*")):
        if not p.is_file() or p.suffix not in SOURCE_EXTENSIONS:
            continue
        if _should_skip(p):
            continue
        try:
            content = p.read_text(errors="replace")
            source_files.append((p.relative_to(project_dir), content))
        except Exception:
            continue

    source_files.sort(key=lambda x: len(x[1]))

    total = sum(len(s) for s in sections)
    for rel, content in source_files:
        truncated = content[:MAX_FILE_SIZE]
        if total + len(truncated) > MAX_TOTAL_CHARS:
            break
        sections.append(f"--- {rel} ---\n{truncated}")
        total += len(truncated)

    tree = _dir_tree(project_dir, max_depth=3)
    return f"--- Directory Structure ---\n{tree}\n\n" + "\n\n".join(sections)


def generate_spec(
    project_dir: Path,
    model: str = "claude-opus-4-7",
    use_cli: bool = True,
) -> Spec:
    codebase_summary = scan_codebase(project_dir)

    prompt = f"""Analyze this codebase and generate a comprehensive feature spec in markdown format.

The spec should document what this project CURRENTLY does (not what it should do).
Read the code carefully and extract:

1. A title and description
2. All implemented features as requirements
3. Acceptance criteria (what a user should be able to do)
4. UI description (if it has a frontend)
5. User flows (step-by-step interactions a user would perform)

Format the spec EXACTLY like this markdown structure:

# <Project Title>

<Description paragraph>

## Requirements

- requirement 1
- requirement 2
...

## Acceptance Criteria

- criterion 1
- criterion 2
...

## UI Description

<describe the UI layout and key screens>

## User Flows

### <Flow Name>
1. Navigate to "/"
2. Click on "<element>"
3. Type "<text>" in "<selector>"
4. Should see "<expected result>"

### <Another Flow>
1. ...

Include at least 3-5 user flows covering the main features.
For selectors, use descriptive text that Playwright can find (button text, input placeholders, link text).

Return ONLY the markdown spec, no code fences or preamble.

## Codebase

{codebase_summary}"""

    if use_cli:
        result = subprocess.run(
            ["claude", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Claude CLI failed: {result.stderr}")
        spec_md = result.stdout
    else:
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=16000,
            messages=[{"role": "user", "content": prompt}],
        )
        spec_md = response.content[0].text

    spec_path = project_dir / ".quito-spec.md"
    spec_path.write_text(spec_md)

    return parse_spec(spec_path)


def _should_skip(p: Path) -> bool:
    parts = p.parts
    skip_dirs = {
        "node_modules",
        ".git",
        ".next",
        "__pycache__",
        "dist",
        "build",
        ".cache",
        "target",
        "vendor",
        ".venv",
        "venv",
        ".turbo",
        ".vercel",
        "coverage",
    }
    return bool(skip_dirs & set(parts))


def _dir_tree(root: Path, max_depth: int = 3, prefix: str = "") -> str:
    if max_depth < 0:
        return ""
    lines = []
    try:
        entries = sorted(root.iterdir(), key=lambda e: (not e.is_dir(), e.name))
    except PermissionError:
        return ""
    dirs = [e for e in entries if e.is_dir() and not _should_skip(e)]
    files = [e for e in entries if e.is_file()]

    for f in files[:20]:
        lines.append(f"{prefix}{f.name}")
    if len(files) > 20:
        lines.append(f"{prefix}... and {len(files) - 20} more files")

    for d in dirs[:15]:
        lines.append(f"{prefix}{d.name}/")
        subtree = _dir_tree(d, max_depth - 1, prefix + "  ")
        if subtree:
            lines.append(subtree)
    if len(dirs) > 15:
        lines.append(f"{prefix}... and {len(dirs) - 15} more dirs")

    return "\n".join(lines)
