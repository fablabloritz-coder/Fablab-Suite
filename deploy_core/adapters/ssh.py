from typing import Callable

from deploy_core.adapters.base import CommandExecutor
from deploy_core.models import CommandResult, StepSpec


RemoteRunner = Callable[[str], tuple[int, str, str]]


class SshCommandExecutor(CommandExecutor):
    """Adapter over an injected remote runner (for example Paramiko wrapper)."""

    def __init__(self, remote_runner: RemoteRunner):
        self.remote_runner = remote_runner

    def run(self, step: StepSpec) -> CommandResult:
        code, out, err = self.remote_runner(step.command)
        return CommandResult(
            step_id=step.step_id,
            label=step.label,
            command=step.command,
            exit_code=code,
            stdout=out,
            stderr=err,
        )
