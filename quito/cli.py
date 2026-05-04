from __future__ import annotations

from pathlib import Path

import click

from .models import RunConfig
from .pipeline import run_pipeline
from .review_pipeline import run_review_pipeline


@click.command()
@click.argument("spec", type=click.Path(exists=True, path_type=Path), required=False, default=None)
@click.option(
    "--project-dir",
    "-d",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Project directory to generate spec from (used when no spec file given)",
)
@click.option(
    "--fix",
    "fix_mode",
    is_flag=True,
    default=False,
    help="Review and fix existing code in-place (on a git branch)",
)
@click.option(
    "--verify",
    "verify_commands",
    type=str,
    multiple=True,
    help="Commands to run after fixes to verify correctness. Repeat for multiple: --verify 'bun test' --verify 'bun typecheck'",
)
@click.option(
    "--output", "-o", type=click.Path(path_type=Path), default="artifacts", help="Output directory for artifacts"
)
@click.option("--max-iterations", "-n", type=int, default=5, help="Max quality loop iterations")
@click.option("--bugbash-agents", type=int, default=100, help="Number of bugbash agents")
@click.option("--bugbash-concurrency", type=int, default=20, help="Max concurrent bugbash browsers")
@click.option("--app-command", type=str, default=None, help="Command to start the dev server")
@click.option("--app-url", type=str, default="http://localhost:3000", help="URL of the running app")
@click.option("--claude-model", type=str, default="claude-opus-4-7", help="Claude model to use")
@click.option("--gemini-model", type=str, default="gemini-2.5-flash", help="Gemini model to use")
@click.option(
    "--reviewer",
    "-r",
    "reviewers",
    type=click.Choice(["codex", "claude", "gemini"]),
    multiple=True,
    default=["codex"],
    help="Reviewer(s) to use. Repeat for multi-review: -r codex -r claude",
)
@click.option("--use-cli/--use-api", default=True, help="Use CLI tools (Max/Pro subs) or APIs (usage-based billing)")
def main(
    spec: Path | None,
    project_dir: Path | None,
    fix_mode: bool,
    verify_commands: tuple[str, ...],
    output: Path,
    max_iterations: int,
    bugbash_agents: int,
    bugbash_concurrency: int,
    app_command: str | None,
    app_url: str,
    claude_model: str,
    gemini_model: str,
    reviewers: tuple[str, ...],
    use_cli: bool,
):
    """Run the Quito quality pipeline.

    \b
    Two modes:
      Default:  Spec -> generate code -> review -> iterate (greenfield)
      --fix:    Review existing code -> apply fixes in-place -> verify (existing projects)

    \b
    Examples:
      quito spec.md                                  # greenfield from spec
      quito -d ~/my-project                          # generate spec, then greenfield
      quito --fix -d ~/my-project -r claude          # review & fix existing code
      quito --fix -d . -r claude --verify 'bun test' # fix with verification
    """
    if not spec and not project_dir:
        project_dir = Path.cwd()

    config = RunConfig(
        spec_path=spec,
        project_dir=project_dir,
        output_dir=output,
        max_iterations=max_iterations,
        bugbash_agents=bugbash_agents,
        bugbash_concurrency=bugbash_concurrency,
        app_command=app_command,
        app_url=app_url,
        claude_model=claude_model,
        gemini_model=gemini_model,
        reviewers=list(reviewers),
        use_cli=use_cli,
    )

    if fix_mode:
        run_review_pipeline(
            config,
            verify_commands=list(verify_commands) if verify_commands else None,
        )
    else:
        run_pipeline(config)


if __name__ == "__main__":
    main()
