from abc import ABC, abstractmethod

from deploy_core.models import CommandResult, StepSpec


class CommandExecutor(ABC):
    @abstractmethod
    def run(self, step: StepSpec) -> CommandResult:
        """Execute one workflow step and return a normalized result."""
        raise NotImplementedError
