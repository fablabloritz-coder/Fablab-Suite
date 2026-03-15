import subprocess

from deploy_core.adapters.base import CommandExecutor
from deploy_core.models import CommandResult, StepSpec


class LocalCommandExecutor(CommandExecutor):
    def __init__(self, working_dir: str = "."):
        self.working_dir = working_dir

    def run(self, step: StepSpec) -> CommandResult:
        proc = subprocess.run(
            step.command,
            cwd=self.working_dir,
            shell=True,
            text=True,
            capture_output=True,
        )
        return CommandResult(
            step_id=step.step_id,
            label=step.label,
            command=step.command,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
