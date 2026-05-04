from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from .agents.claude import ClaudeCodegen
from .agents.claude_review import ClaudeReview
from .agents.codex import CodexReview
from .agents.gemini import GeminiVisualQA
from .agents.gemini_review import GeminiReview
from .agents.multi_review import MultiReview
from .browser.capture import run_capture
from .models import GateDecision, IterationResult, RunConfig
from .stages.base import CodegenStage, PipelineContext, ReviewStage, Stage, VisualQAStage
from .stages.bugbash import deduplicate_findings, generate_personas, run_bugbash
from .stages.gate import evaluate_gate
from .stages.spec_gen import generate_spec
from .stages.spec_parse import parse_spec
from .store import RunStore

console = Console()


REVIEWER_REGISTRY: dict[str, type[ReviewStage]] = {
    "codex": CodexReview,
    "claude": ClaudeReview,
    "gemini": GeminiReview,
}


def build_reviewer(names: list[str], config: RunConfig) -> ReviewStage:
    reviewers = []
    for name in names:
        cls = REVIEWER_REGISTRY.get(name)
        if not cls:
            raise ValueError(f"Unknown reviewer: {name}. Available: {', '.join(REVIEWER_REGISTRY)}")
        if name == "claude":
            reviewers.append(cls(model=config.claude_model, use_cli=config.use_cli))
        elif name == "gemini":
            reviewers.append(cls(model=config.gemini_model, use_cli=config.use_cli))
        else:
            reviewers.append(cls(use_cli=config.use_cli))
    if len(reviewers) == 1:
        return reviewers[0]
    return MultiReview(reviewers)


def default_stages(config: RunConfig) -> dict[str, Stage]:
    reviewer = build_reviewer(config.reviewers, config)
    return {
        "codegen": ClaudeCodegen(model=config.claude_model, use_cli=config.use_cli),
        "review": reviewer,
        "visual_qa": GeminiVisualQA(model=config.gemini_model, use_cli=config.use_cli),
    }


def run_pipeline(
    config: RunConfig,
    stages: dict[str, Stage] | None = None,
    extra_stages: list[Stage] | None = None,
) -> Path:
    console.print(Panel("Quito Pipeline", style="bold cyan"))

    if config.spec_path:
        spec = parse_spec(config.spec_path)
        console.print(f"Spec: [bold]{spec.title}[/bold] (from file)")
    elif config.project_dir:
        console.print(f"[cyan]Generating spec from codebase:[/cyan] {config.project_dir}")
        spec = generate_spec(
            config.project_dir,
            model=config.claude_model,
            use_cli=config.use_cli,
        )
        console.print(f"Spec: [bold]{spec.title}[/bold] (generated)")
        console.print(f"  Saved to {config.project_dir / '.quito-spec.md'}")
    else:
        raise ValueError("Either spec_path or project_dir must be provided")

    console.print(f"  {len(spec.requirements)} requirements, {len(spec.acceptance_criteria)} acceptance criteria")

    output_dir = config.output_dir or Path("artifacts")
    store = RunStore(output_dir)
    store.save_spec(spec)
    console.print(f"Run: [dim]{store.run_id}[/dim] -> {store.root}")

    s = stages or default_stages(config)
    codegen: CodegenStage = s["codegen"]
    reviewer: ReviewStage = s["review"]
    visual_qa: VisualQAStage = s["visual_qa"]

    console.print(
        f"Stages: codegen=[bold]{codegen.name}[/bold] review=[bold]{reviewer.name}[/bold] visual_qa=[bold]{visual_qa.name}[/bold]"
    )

    dev_server = None
    code: dict[str, str] = {}
    feedback = []
    gate = None

    try:
        for iteration in range(1, config.max_iterations + 1):
            console.print(f"\n[bold yellow]--- Iteration {iteration}/{config.max_iterations} ---[/bold yellow]")

            # Stage 1: Code generation / update
            if iteration == 1:
                console.print(f"[cyan]Stage 1:[/cyan] Generating code ({codegen.name})...")
                plan, code = codegen.generate(spec, None, None)
            else:
                console.print(f"[cyan]Stage 1:[/cyan] Updating code from feedback ({codegen.name})...")
                plan, code = codegen.generate(spec, feedback=feedback, existing_code=code)

            store.save_plan(iteration, plan)
            for path, content in code.items():
                store.save_code_file(iteration, path, content)
            console.print(f"  Generated {len(code)} files")

            # Stage 2: Code review
            console.print(f"[cyan]Stage 2:[/cyan] Reviewing code ({reviewer.name})...")
            review_comments = reviewer.review(code, spec, plan)
            store.save_review(iteration, review_comments)
            _print_findings("Review", review_comments)

            # Stage 3: Apply review
            if review_comments:
                console.print(f"[cyan]Stage 3:[/cyan] Applying review feedback ({codegen.name})...")
                code, responses = codegen.apply_review(code, review_comments, spec)
                store.save_update_response(iteration, {"responses": responses})
                for path, content in code.items():
                    store.save_code_file(iteration, path, content)
                console.print(f"  Applied {len(responses)} responses")

            # Extra stages (user-provided plugins)
            if extra_stages:
                ctx = PipelineContext(spec, store, config)
                ctx.iteration = iteration
                ctx.code = code
                ctx.plan = plan
                ctx.review_comments = review_comments
                for stage in extra_stages:
                    console.print(f"[cyan]Plugin:[/cyan] {stage.name}...")
                    ctx = stage.run(ctx)
                code = ctx.code

            # Stage 4: Visual QA (requires running app)
            visual_findings = []
            if config.app_command:
                console.print(f"[cyan]Stage 4:[/cyan] Visual QA ({visual_qa.name})...")

                _write_code_to_disk(code, store.code_dir(iteration))

                if dev_server:
                    dev_server.terminate()
                    dev_server.wait(timeout=5)
                dev_server = _start_dev_server(config.app_command, store.code_dir(iteration))
                time.sleep(5)

                screenshots_dir = store.screenshots_dir(iteration)
                video_path = store.video_path(iteration)
                screenshot_paths = run_capture(spec, config.app_url, screenshots_dir, video_path)
                console.print(f"  Captured {len(screenshot_paths)} screenshots")

                if screenshot_paths:
                    screenshot_findings = visual_qa.review_screenshots(screenshot_paths, spec)
                    visual_findings.extend(screenshot_findings)

                if video_path.exists():
                    video_findings = visual_qa.review_video(video_path, spec)
                    visual_findings.extend(video_findings)

                store.save_visual_feedback(iteration, visual_findings)
                _print_findings("Visual QA", visual_findings)
            else:
                console.print("[dim]Stage 4: Skipped (no app_command configured)[/dim]")

            # Stage 5: Gate decision
            gate = evaluate_gate(iteration, config.max_iterations, review_comments, visual_findings)
            store.save_gate(iteration, gate)

            result = IterationResult(
                iteration=iteration,
                review_comments=review_comments,
                visual_findings=visual_findings,
                gate=gate,
            )
            store.save_iteration_result(result)

            console.print(f"\n[bold]Gate:[/bold] {gate.decision.value} -- {gate.reason}")

            if gate.decision == GateDecision.PROCEED_TO_BUGBASH:
                break
            elif gate.decision == GateDecision.HALT:
                console.print("[bold red]Halting -- max iterations reached with unresolved issues[/bold red]")
                break

            feedback = review_comments

        # Bugbash phase
        if gate and gate.decision == GateDecision.PROCEED_TO_BUGBASH and config.bugbash_agents > 0:
            console.print(f"\n[bold magenta]--- Bugbash ({config.bugbash_agents} agents) ---[/bold magenta]")

            if config.app_command and not dev_server:
                dev_server = _start_dev_server(config.app_command, store.code_dir(iteration))
                time.sleep(5)

            console.print("Generating personas...")
            personas = generate_personas(spec, config.bugbash_agents)
            store.save_personas(personas)
            console.print(f"  {len(personas)} personas ready")

            if config.app_command:
                console.print(f"Running bugbash (concurrency: {config.bugbash_concurrency})...")
                findings = asyncio.run(
                    run_bugbash(
                        personas,
                        spec,
                        config.app_url,
                        store,
                        config.bugbash_concurrency,
                    )
                )
                console.print(f"  {len(findings)} raw findings")

                if findings:
                    console.print("Deduplicating findings...")
                    clustered, summary = deduplicate_findings(findings)
                    store.save_clustered_findings(clustered)
                    store.save_bugbash_report(summary)
                    console.print(f"  {len(clustered)} unique bugs")

                    critical_bugs = [c for c in clustered if c.get("severity") == "critical"]
                    if critical_bugs:
                        console.print(f"[bold red]  {len(critical_bugs)} critical bugs found[/bold red]")
            else:
                console.print("[dim]Bugbash browser testing skipped (no app_command)[/dim]")

        store.save_summary(_generate_summary(store, iteration))
        console.print(f"\n[bold green]Done.[/bold green] Artifacts: {store.root}")

    finally:
        if dev_server:
            dev_server.terminate()
            try:
                dev_server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                dev_server.kill()

    return store.root


def _start_dev_server(command: str, cwd: Path) -> subprocess.Popen:
    return subprocess.Popen(
        command,
        shell=True,
        cwd=str(cwd),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _write_code_to_disk(code: dict[str, str], target_dir: Path):
    for path, content in code.items():
        full_path = target_dir / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)


def _print_findings(label: str, items):
    if not items:
        console.print(f"  [green]{label}: No issues[/green]")
        return
    by_severity = {}
    for item in items:
        sev = item.severity.value if hasattr(item, "severity") else "unknown"
        by_severity[sev] = by_severity.get(sev, 0) + 1
    parts = []
    for sev in ["critical", "high", "medium", "low", "info"]:
        if sev in by_severity:
            color = {"critical": "red", "high": "yellow", "medium": "blue", "low": "dim", "info": "dim"}[sev]
            parts.append(f"[{color}]{by_severity[sev]} {sev}[/{color}]")
    console.print(f"  {label}: {', '.join(parts)}")


def _generate_summary(store: RunStore, final_iteration: int) -> str:
    lines = ["# Quito Run Summary\n"]
    spec = store.load_spec()
    lines.append(f"**Spec:** {spec.title}\n")
    lines.append(f"**Iterations:** {final_iteration}\n")

    for i in range(1, final_iteration + 1):
        gate = store.load_gate(i)
        review = store.load_review(i)
        visual = store.load_visual_feedback(i)
        lines.append(f"\n## Iteration {i}")
        lines.append(f"- Review comments: {len(review)}")
        lines.append(f"- Visual findings: {len(visual)}")
        if gate:
            lines.append(f"- Gate: {gate.decision.value} -- {gate.reason}")

    findings = store.load_bugbash_findings()
    if findings:
        lines.append("\n## Bugbash")
        lines.append(f"- Raw findings: {len(findings)}")
        clustered_path = store.bugbash_dir() / "clustered.json"
        if clustered_path.exists():
            import json

            clustered = json.loads(clustered_path.read_text())
            lines.append(f"- Unique bugs: {len(clustered)}")

    return "\n".join(lines)
