from __future__ import annotations

import subprocess
from pathlib import Path

from .base import VerifyStage


class CommandVerify(VerifyStage):
    """Runs a list of shell commands to verify the project builds/passes."""

    name = "commands"

    def __init__(self, commands: list[str]):
        self.commands = commands

    def verify(self, project_dir: Path) -> list[dict]:
        results = []
        for cmd in self.commands:
            try:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=str(project_dir),
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                results.append(
                    {
                        "command": cmd,
                        "passed": result.returncode == 0,
                        "output": (result.stdout + result.stderr)[-2000:],
                    }
                )
            except subprocess.TimeoutExpired:
                results.append(
                    {
                        "command": cmd,
                        "passed": False,
                        "output": "Command timed out after 300s",
                    }
                )
        return results
