from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from .agents.claude_fixer import ClaudeFixer
from .agents.claude_review import ClaudeReview
from .agents.codex import CodexReview
from .agents.gemini import GeminiVisualQA
from .agents.gemini_review import GeminiReview
from .agents.multi_review import MultiReview
from .browser.capture import run_capture
from .models import GateDecision, RunConfig, Severity
from .stages.base import ReviewStage
from .stages.bugbash import deduplicate_findings, generate_personas, run_bugbash
from .stages.gate import evaluate_gate
from .stages.spec_gen import SOURCE_EXTENSIONS, _should_skip, generate_spec
from .stages.spec_parse import parse_spec
from .stages.verify import CommandVerify
from .store import RunStore

console = Console()

REVIEWER_REGISTRY: dict[str, type[ReviewStage]] = {
    "codex": CodexReview,
    "claude": ClaudeReview,
    "gemini": GeminiReview,
}


def _build_reviewer(names: list[str], config: RunConfig) -> ReviewStage:
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


def _read_project_files(project_dir: Path) -> dict[str, str]:
    """Read all source files from the project directory."""
    files = {}
    for p in sorted(project_dir.rglob("*")):
        if not p.is_file() or p.suffix not in SOURCE_EXTENSIONS:
            continue
        if _should_skip(p):
            continue
        try:
            content = p.read_text(errors="replace")
            rel = str(p.relative_to(project_dir))
            files[rel] = content
        except Exception:
            continue
    return files


def _create_branch(project_dir: Path, run_id: str) -> str | None:
    """Create a git branch for fixes. Returns branch name or None if not a git repo."""
    git_dir = project_dir / ".git"
    if not git_dir.exists():
        return None

    branch_name = f"quito/fix-{run_id}"
    result = subprocess.run(
        ["git", "checkout", "-b", branch_name],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return branch_name


def _git_diff(project_dir: Path) -> str:
    """Get the current git diff."""
    result = subprocess.run(
        ["git", "diff"],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def _git_commit(project_dir: Path, message: str) -> bool:
    """Stage all changes and commit."""
    subprocess.run(["git", "add", "-A"], cwd=str(project_dir), capture_output=True)
    result = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def run_review_pipeline(
    config: RunConfig,
    verify_commands: list[str] | None = None,
) -> Path:
    """Review and fix an existing codebase in-place."""
    console.print(Panel("Quito Review & Fix", style="bold cyan"))

    project_dir = config.project_dir
    if not project_dir:
        raise ValueError("project_dir is required for review pipeline")

    # Generate or load spec
    if config.spec_path:
        spec = parse_spec(config.spec_path)
        console.print(f"Spec: [bold]{spec.title}[/bold] (from file)")
    else:
        console.print(f"[cyan]Generating spec from codebase:[/cyan] {project_dir}")
        spec = generate_spec(project_dir, model=config.claude_model, use_cli=config.use_cli)
        console.print(f"Spec: [bold]{spec.title}[/bold] (generated)")
        console.print(f"  {len(spec.requirements)} requirements, {len(spec.acceptance_criteria)} acceptance criteria")

    # Set up artifact store
    output_dir = config.output_dir or Path("artifacts")
    store = RunStore(output_dir)
    store.save_spec(spec)
    console.print(f"Run: [dim]{store.run_id}[/dim] -> {store.root}")

    # Create git branch
    branch = _create_branch(project_dir, store.run_id[:8])
    if branch:
        console.print(f"Branch: [bold]{branch}[/bold]")
    else:
        console.print("[yellow]Not a git repo -- fixes will be applied directly (no branch safety)[/yellow]")

    # Set up stages
    reviewer = _build_reviewer(config.reviewers, config)
    fixer = ClaudeFixer(model=config.claude_model, use_cli=config.use_cli)
    verifier = CommandVerify(verify_commands) if verify_commands else None
    visual_qa = (
        GeminiVisualQA(model=config.gemini_model, use_cli=config.use_cli, project_dir=project_dir)
        if config.app_command
        else None
    )

    console.print(f"Reviewer: [bold]{reviewer.name}[/bold] | Fixer: [bold]{fixer.name}[/bold]")
    if verifier:
        console.print(f"Verify commands: {verify_commands}")
    if visual_qa:
        console.print(f"Visual QA: [bold]{visual_qa.name}[/bold] | App: {config.app_url}")

    dev_server = None

    try:
        for iteration in range(1, config.max_iterations + 1):
            console.print(f"\n[bold yellow]--- Iteration {iteration}/{config.max_iterations} ---[/bold yellow]")

            console.print("[cyan]Reading project files...[/cyan]")
            code = _read_project_files(project_dir)
            console.print(f"  {len(code)} source files")

            console.print(f"[cyan]Reviewing code ({reviewer.name})...[/cyan]")
            review_comments = reviewer.review(code, spec, "")
            store.save_review(iteration, review_comments)
            _print_findings("Review", review_comments)

            fixable = [c for c in review_comments if c.severity in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM)]
            if not fixable:
                console.print("[bold green]No issues to fix -- code looks good![/bold green]")
                gate = evaluate_gate(iteration, config.max_iterations, review_comments, [])
                store.save_gate(iteration, gate)
                break

            console.print(f"[cyan]Applying fixes ({fixer.name})...[/cyan]")
            actions = fixer.fix(project_dir, review_comments, spec)
            store.save_update_response(iteration, {"actions": actions})

            fixed_count = sum(1 for a in actions if a.get("action") == "fixed")
            declined_count = sum(1 for a in actions if a.get("action") == "declined")
            skipped_count = sum(1 for a in actions if a.get("action") == "skipped")
            console.print(f"  {fixed_count} fixed, {declined_count} declined, {skipped_count} skipped")

            diff = _git_diff(project_dir)
            if diff:
                diff_lines = diff.count("\n")
                store.root.joinpath(f"iteration-{iteration}").mkdir(parents=True, exist_ok=True)
                (store.root / f"iteration-{iteration}" / "diff.patch").write_text(diff)
                console.print(f"  Diff: {diff_lines} lines (saved to artifacts)")

            if verifier:
                console.print("[cyan]Verifying fixes...[/cyan]")
                verify_results = verifier.verify(project_dir)
                store.root.joinpath(f"iteration-{iteration}").mkdir(parents=True, exist_ok=True)
                (store.root / f"iteration-{iteration}" / "verify.json").write_text(json.dumps(verify_results, indent=2))

                all_passed = True
                for vr in verify_results:
                    status = "[green]PASS[/green]" if vr["passed"] else "[red]FAIL[/red]"
                    console.print(f"  {status} {vr['command']}")
                    if not vr["passed"]:
                        all_passed = False

                if not all_passed:
                    console.print(f"[cyan]Fixing build errors ({fixer.name})...[/cyan]")
                    build_actions = fixer.fix_build(project_dir, verify_results, spec)
                    bfixed = sum(1 for a in build_actions if a.get("action") == "fixed")
                    if bfixed:
                        console.print(f"  {bfixed} build fixes applied")
                        re_verify = verifier.verify(project_dir)
                        re_passed = all(v["passed"] for v in re_verify)
                        for vr in re_verify:
                            status = "[green]PASS[/green]" if vr["passed"] else "[red]FAIL[/red]"
                            console.print(f"  {status} {vr['command']}")
                        if not re_passed:
                            console.print("[yellow]Build still failing after fix attempt[/yellow]")
                    else:
                        console.print("[yellow]No build fixes applied -- verification still failing[/yellow]")

            visual_findings = []
            if visual_qa and config.app_command:
                console.print(f"[cyan]Visual QA ({visual_qa.name})...[/cyan]")
                try:
                    if dev_server:
                        dev_server.terminate()
                        dev_server.wait(timeout=5)
                    dev_server = _start_dev_server(config.app_command, project_dir)
                    time.sleep(8)

                    screenshots_dir = store.screenshots_dir(iteration)
                    video_path = store.video_path(iteration)
                    screenshot_paths = run_capture(spec, config.app_url, screenshots_dir, video_path)
                    console.print(f"  Captured {len(screenshot_paths)} screenshots")

                    if screenshot_paths:
                        visual_findings.extend(visual_qa.review_screenshots(screenshot_paths, spec))
                    if video_path.exists():
                        visual_findings.extend(visual_qa.review_video(video_path, spec))

                    store.save_visual_feedback(iteration, visual_findings)
                except Exception as exc:
                    console.print(f"  [yellow]Visual QA failed: {exc}[/yellow]")
                    console.print("  [dim]Continuing without visual QA results[/dim]")
                _print_findings("Visual QA", visual_findings)

                visual_fixable = [
                    f for f in visual_findings if f.severity in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM)
                ]
                if visual_fixable:
                    try:
                        console.print(f"[cyan]Fixing {len(visual_fixable)} visual issues ({fixer.name})...[/cyan]")
                        visual_actions = fixer.fix_visual(project_dir, visual_findings, spec)
                        vfixed = sum(1 for a in visual_actions if a.get("action") == "fixed")
                        console.print(f"  {vfixed} visual fixes applied")

                        if vfixed > 0:
                            dev_server.terminate()
                            dev_server.wait(timeout=5)
                            dev_server = _start_dev_server(config.app_command, project_dir)
                            time.sleep(8)

                            console.print("[cyan]Re-capturing after visual fixes...[/cyan]")
                            recheck_dir = store.iteration_dir(iteration) / "screenshots-recheck"
                            recheck_video = store.iteration_dir(iteration) / "recording-recheck.webm"
                            recheck_shots = run_capture(spec, config.app_url, recheck_dir, recheck_video)
                            console.print(f"  Captured {len(recheck_shots)} verification screenshots")

                            recheck_findings = []
                            if recheck_shots:
                                recheck_findings.extend(visual_qa.review_screenshots(recheck_shots, spec))
                            if recheck_video.exists():
                                recheck_findings.extend(visual_qa.review_video(recheck_video, spec))

                            remaining = [
                                f
                                for f in recheck_findings
                                if f.severity in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM)
                            ]
                            if remaining:
                                console.print(f"  [yellow]{len(remaining)} visual issues remain after fix[/yellow]")
                            else:
                                console.print("  [green]Visual issues resolved[/green]")
                            visual_findings = recheck_findings
                    except Exception as exc:
                        console.print(f"  [yellow]Visual fix/re-verify failed: {exc}[/yellow]")
                        console.print("  [dim]Continuing with original visual findings[/dim]")

            if branch and fixed_count > 0:
                _git_commit(project_dir, f"quito: fix {fixed_count} issues (iteration {iteration})")
                console.print("  [dim]Committed fixes[/dim]")

            gate = evaluate_gate(iteration, config.max_iterations, review_comments, visual_findings)
            store.save_gate(iteration, gate)
            console.print(f"\n[bold]Gate:[/bold] {gate.decision.value} -- {gate.reason}")

            if gate.decision == GateDecision.PROCEED_TO_BUGBASH:
                console.print("[bold green]All critical/high issues resolved![/bold green]")
                break
            elif gate.decision == GateDecision.HALT:
                console.print("[bold red]Max iterations reached with unresolved issues[/bold red]")
                break

        if (
            gate
            and gate.decision == GateDecision.PROCEED_TO_BUGBASH
            and config.bugbash_agents > 0
            and config.app_command
        ):
            console.print(f"\n[bold magenta]--- Bugbash ({config.bugbash_agents} agents) ---[/bold magenta]")

            if not dev_server:
                dev_server = _start_dev_server(config.app_command, project_dir)
                time.sleep(8)

            console.print("Generating personas...")
            personas = generate_personas(spec, config.bugbash_agents, use_cli=config.use_cli)
            store.save_personas(personas)
            console.print(f"  {len(personas)} personas ready")

            console.print(f"Running bugbash (concurrency: {config.bugbash_concurrency})...")
            findings = asyncio.run(
                run_bugbash(
                    personas,
                    spec,
                    config.app_url,
                    store,
                    config.bugbash_concurrency,
                    use_cli=config.use_cli,
                )
            )
            console.print(f"  {len(findings)} raw findings")

            if findings:
                console.print("Deduplicating findings...")
                clustered, summary = deduplicate_findings(findings, use_cli=config.use_cli)
                store.save_clustered_findings(clustered)
                store.save_bugbash_report(summary)
                console.print(f"  {len(clustered)} unique bugs")

                critical_bugs = [c for c in clustered if c.get("severity") == "critical"]
                if critical_bugs:
                    console.print(f"[bold red]  {len(critical_bugs)} critical bugs found[/bold red]")

        store.save_summary(_generate_review_summary(store, iteration, branch, project_dir))

        console.print(f"\n[bold green]Done.[/bold green] Artifacts: {store.root}")
        if branch:
            console.print(f"Fixes on branch: [bold]{branch}[/bold]")
            console.print(f"  Review: git log {branch}")
            console.print(f"  Diff:   git diff main..{branch}")

    finally:
        if dev_server:
            dev_server.terminate()
            try:
                dev_server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                dev_server.kill()

    return store.root


def _build_workspace_deps(project_dir: Path) -> None:
    root_pkg = project_dir / "package.json"
    if not root_pkg.exists():
        return
    try:
        root = json.loads(root_pkg.read_text())
    except Exception:
        return
    workspaces = root.get("workspaces", [])
    if not workspaces:
        return

    for ws in workspaces:
        ws_dir = project_dir / ws
        ws_pkg = ws_dir / "package.json"
        if not ws_pkg.exists():
            continue
        try:
            pkg = json.loads(ws_pkg.read_text())
        except Exception:
            continue
        main_file = pkg.get("main") or pkg.get("module")
        if not main_file:
            continue
        if (ws_dir / main_file).exists():
            continue
        scripts = pkg.get("scripts", {})
        if "build" not in scripts:
            continue
        console.print(f"  [dim]Building workspace dep: {ws}[/dim]")
        subprocess.run(
            ["bun", "run", "build"],
            cwd=str(ws_dir),
            capture_output=True,
            timeout=120,
        )


def _start_dev_server(command: str, cwd: Path) -> subprocess.Popen:
    _build_workspace_deps(cwd)
    return subprocess.Popen(
        command,
        shell=True,
        cwd=str(cwd),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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


def _generate_review_summary(store: RunStore, final_iteration: int, branch: str | None, project_dir: Path) -> str:
    lines = ["# Quito Review & Fix Summary\n"]
    spec = store.load_spec()
    lines.append(f"**Project:** {project_dir}")
    lines.append(f"**Spec:** {spec.title}")
    lines.append(f"**Iterations:** {final_iteration}")
    if branch:
        lines.append(f"**Branch:** {branch}\n")

    for i in range(1, final_iteration + 1):
        gate = store.load_gate(i)
        review = store.load_review(i)
        lines.append(f"\n## Iteration {i}")
        lines.append(f"- Review comments: {len(review)}")

        by_sev = {}
        for c in review:
            by_sev[c.severity.value] = by_sev.get(c.severity.value, 0) + 1
        if by_sev:
            lines.append(f"- Breakdown: {', '.join(f'{v} {k}' for k, v in by_sev.items())}")

        if gate:
            lines.append(f"- Gate: {gate.decision.value} -- {gate.reason}")

    return "\n".join(lines)
