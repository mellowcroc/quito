from __future__ import annotations

from pathlib import Path

import click

from .models import RunConfig
from .pipeline import run_pipeline


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

    Pass a SPEC file to use an existing spec, or use --project-dir (-d) to
    auto-generate a spec from an existing codebase.

    \b
    Examples:
      quito spec.md                          # run with explicit spec
      quito -d ~/my-project                  # generate spec from codebase
      quito -d . -r codex -r claude          # multi-review on current dir
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

    run_pipeline(config)


if __name__ == "__main__":
    main()
