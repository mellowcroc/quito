# Quito Architecture

A pluggable multi-AI quality pipeline for software development. Spec in, production-ready code out.

Each iteration refines code through multiple AI lenses, followed by adversarial testing at scale. Every stage (codegen, review, visual QA) is swappable via a plugin interface.

## Pipeline Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      ORCHESTRATOR                               │
│                     (Python/asyncio)                            │
│                                                                 │
│  ┌──────┐   ┌────────┐   ┌──────┐   ┌─────────┐   ┌────┐     │
│  │ Spec │──▶│Codegen │──▶│Review│──▶│ Update  │──▶│ QA │     │
│  │Parse │   │ Stage  │   │Stage │   │ Stage   │   │Gate│     │
│  └──────┘   └────────┘   └──────┘   └─────────┘   └──┬─┘     │
│                 ▲                                      │        │
│                 │    ┌──────────┐   ┌──────────┐      │        │
│                 │    │ Plugin   │   │ Plugin   │      │        │
│                 │    │ Stage A  │   │ Stage B  │      │        │
│                 │    └────┬─────┘   └────┬─────┘      │        │
│                 │         └──────┬───────┘             │        │
│                 │         ┌──────┴───────┐             │        │
│                 └─────────│  Feedback    │◀────────────┘        │
│                           │  Aggregator  │                      │
│                           └──────┬───────┘                      │
│                                  ▲                              │
│                    ┌─────────────┼─────────────┐                │
│                    │             │             │                │
│              ┌─────┴───┐  ┌─────┴───┐  ┌─────┴─────┐          │
│              │Visual QA│  │Visual QA│  │ Bugbash   │          │
│              │ Screen  │  │ Video   │  │ Swarm     │          │
│              └─────────┘  └─────────┘  └───────────┘          │
└─────────────────────────────────────────────────────────────────┘
```

## Plugin Interface

All stages implement a base `Stage` class. The three core stage types have specialized interfaces:

```python
from quito import Stage, CodegenStage, ReviewStage, VisualQAStage, run_pipeline, RunConfig

# Custom codegen using a different model
class MyCodegen(CodegenStage):
    name = "my-codegen"

    def generate(self, spec, feedback, existing_code):
        # your implementation
        return plan, files

    def apply_review(self, code, comments, spec):
        # your implementation
        return updated_files, responses

# Custom review stage (e.g. using a linter or different LLM)
class LintReview(ReviewStage):
    name = "eslint"

    def review(self, code, spec, plan):
        # run eslint, return ReviewComment list
        return comments

# Custom visual QA (e.g. using a different vision model)
class MyVisualQA(VisualQAStage):
    name = "my-visual-qa"

    def review_screenshots(self, screenshot_paths, spec):
        return findings

    def review_video(self, video_path, spec):
        return findings

# Extra stages run between review and visual QA
class TypeCheckStage(Stage):
    name = "typecheck"

    def run(self, ctx):
        # run tsc, modify ctx as needed
        return ctx

# Wire it up
config = RunConfig(spec_path="spec.md")
run_pipeline(
    config,
    stages={
        "codegen": MyCodegen(),
        "review": LintReview(),
        "visual_qa": MyVisualQA(),
    },
    extra_stages=[TypeCheckStage()],
)
```

## CLI vs API Mode

All built-in agents support both CLI and API modes:

| Agent | CLI tool | API |
|-------|----------|-----|
| Claude (codegen/update) | `claude` CLI (Max/Pro sub) | Anthropic API |
| Codex (review) | `codex` CLI (ChatGPT sub) | OpenAI API |
| Gemini (visual QA) | `gemini` CLI (Google AI Studio) | Google GenAI SDK |

Default is `--use-cli` (uses your existing subscriptions, no API keys needed).
Use `--use-api` for programmatic access with API keys.

## Artifact Store

Every stage reads from and writes to a run directory. No agents talk to each other directly.

```
artifacts/run-{uuid}/
├── spec.md                          # original spec
├── iteration-{n}/
│   ├── plan.md                      # codegen stage plan
│   ├── code/                        # generated/updated source
│   ├── review.json                  # review stage comments
│   ├── update_response.json         # codegen response to each review item
│   ├── screenshots/
│   ├── recording.webm               # playwright video
│   ├── visual_feedback.json         # visual QA feedback
│   └── gate_decision.json           # proceed / loop back / halt
├── bugbash/
│   ├── personas/
│   ├── findings/
│   ├── clustered.json
│   └── report.md
└── summary.md
```

## Stage Details

### Stage 1 -- Spec Parse

Read markdown spec, extract sections (requirements, acceptance criteria, UI descriptions, user flows). Output a structured object the other stages reference.

### Stage 2 -- Code Generation (default: Claude)

On iteration 1, generates code from spec. On iteration 2+, gets aggregated feedback from review + visual QA so it addresses everything in one pass.

### Stage 3 -- Code Review (default: Codex)

Reviews generated code for correctness, security, spec compliance, and edge cases. Returns structured comments.

### Stage 4 -- Code Update (default: Claude)

Applies review feedback. For each comment, either applies the fix or explains disagreement.

### Plugin Stages

User-provided stages run here, between review and visual QA. Use for linting, type checking, test running, or any custom validation.

### Stage 5 -- Visual QA (default: Gemini)

Two sub-stages:

**Screenshots**: Playwright walks user flows, captures screenshots. Sent to vision model for layout/content review.

**Video**: Playwright records interactions. Sent to vision model to catch transition jank, hover states, scroll behavior, loading states.

### Stage 6 -- Gate Decision

Aggregate all feedback. No critical/high issues = proceed to bugbash. Otherwise loop back. Max 5 iterations.

## Bugbash Swarm

Runs after the quality loop converges. Three phases:

### Phase 1 -- Persona Generation

100 diverse attack personas:

- 20 security (XSS, injection, auth bypass, CSRF)
- 15 edge cases (empty inputs, unicode, max lengths)
- 15 accessibility (screen reader, keyboard-only, color contrast)
- 10 performance (rapid clicks, large payloads, slow network)
- 10 mobile/responsive (various viewports, touch events)
- 10 state corruption (back button, tab duplication, stale data)
- 10 concurrency (race conditions, double submit)
- 10 adversarial UX (confusing flows, misleading states)

### Phase 2 -- Parallel Execution

Each agent gets its own Playwright browser context. Concurrency capped at 20 via asyncio.Semaphore. Up to 50 actions per agent.

### Phase 3 -- Dedup + Triage

All findings clustered, deduplicated, severity-assigned, and reported.

## Convergence & Termination

- Quality loop: max 5 iterations, exits early when gate passes
- Bugbash: runs once, findings feed back into one final iteration
- Final output: code + comprehensive report

## Tech Stack

| Component | Tool |
|-----------|------|
| Orchestrator | Python 3.12 + asyncio |
| Code generation | Claude CLI or Anthropic API |
| Code review | Codex CLI or OpenAI API |
| Visual QA | Gemini CLI or Google GenAI SDK + Playwright |
| Browser automation | Playwright (screenshots, video, interaction) |
| Bugbash agents | Anthropic SDK + Playwright per agent |
| Artifact store | Local filesystem |
| Agent parallelism | asyncio.Semaphore (20 concurrent) |
