from deploy_core.adapters.base import CommandExecutor
from deploy_core.models import Operation, WorkflowContext, WorkflowResult
from deploy_core.workflows import build_workflow


class DeploymentService:
    def __init__(self, executor: CommandExecutor):
        self.executor = executor

    def run(self, operation: Operation, context: WorkflowContext) -> WorkflowResult:
        steps = build_workflow(operation, context)
        results = []
        stopped_early = False

        for step in steps:
            result = self.executor.run(step)
            results.append(result)
            if result.exit_code != 0 and step.stop_on_failure:
                stopped_early = True
                break

        return WorkflowResult(
            operation=operation,
            results=results,
            stopped_early=stopped_early,
        )
