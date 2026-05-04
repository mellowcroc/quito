# Quito

Pluggable multi-AI quality pipeline for software development.

## Build & Install

```bash
pip install -e '.[dev]'
playwright install chromium
git config core.hooksPath .githooks
```

## Run

```bash
# Greenfield: generate code from spec
quito spec.md
quito -d ~/some-project

# Review & fix existing code in-place (creates a git branch)
quito --fix -d ~/my-project -r claude
quito --fix -d . -r claude --verify 'bun test' --verify 'bun typecheck'
```

## Project Structure

- `quito/pipeline.py` — main orchestrator loop, wires all stages together
- `quito/models.py` — all Pydantic data models (Spec, RunConfig, ReviewComment, etc.)
- `quito/store.py` — artifact store, manages per-run directories and file I/O
- `quito/cli.py` — Click CLI entry point
- `quito/review_pipeline.py` — review & fix pipeline for existing projects (--fix mode)
- `quito/stages/base.py` — plugin interface: Stage, CodegenStage, ReviewStage, VisualQAStage, FixStage, VerifyStage
- `quito/stages/spec_parse.py` — markdown spec parser
- `quito/stages/spec_gen.py` — generates a spec from an existing codebase via Claude
- `quito/stages/gate.py` — pass/fail/loop decision logic
- `quito/stages/bugbash.py` — persona generation, parallel agent execution, dedup
- `quito/agents/claude.py` — Claude codegen (API + CLI)
- `quito/agents/codex.py` — Codex code review (API + CLI)
- `quito/agents/gemini.py` — Gemini visual QA with screenshots + video (API + CLI)
- `quito/agents/claude_review.py` — Claude as reviewer
- `quito/agents/gemini_review.py` — Gemini as reviewer
- `quito/agents/multi_review.py` — composite that merges findings from multiple reviewers
- `quito/agents/claude_fixer.py` — applies fixes to actual project files per-file
- `quito/stages/verify.py` — runs project commands (tests, typecheck) after fixes
- `quito/browser/capture.py` — Playwright screenshot + video capture

## Git

- Never append Co-Authored-By or any AI-attribution trailer to commits
- Never use backslash line-continuations in shell commands

## Conventions

- Python 3.12+, type hints everywhere, `from __future__ import annotations` at top of every file
- Pydantic v2 for all data models
- All agents support both `--use-cli` (shells out to claude/codex/gemini CLIs) and `--use-api` (direct SDK calls)
- Relative imports within the package (e.g. `from ..models import Spec`)
- No comments unless explaining a non-obvious why
- JSON parsing helpers handle markdown fences and partial JSON from LLM responses
- Stages communicate only through the artifact store (RunStore), never directly

## Pre-commit Hook

`.githooks/pre-commit` runs on every commit:
1. `py_compile` — syntax check on staged .py files
2. `ruff check` — lint (pyflakes, bugbear, bandit security, isort)
3. `ruff format --check` — formatting

Fix lint: `ruff check --fix`. Fix formatting: `ruff format`.
