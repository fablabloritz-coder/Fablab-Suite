"""Small demo entry point for the deploy_core scaffold.

Usage examples:
- python -m deploy_core.demo local status
- python -m deploy_core.demo local update
"""

import argparse

from deploy_core.adapters.local import LocalCommandExecutor
from deploy_core.models import DeploymentMode, Operation, WorkflowContext
from deploy_core.service import DeploymentService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="deploy_core demo")
    parser.add_argument("mode", choices=["local"], help="Execution mode")
    parser.add_argument("operation", choices=[o.value for o in Operation], help="Operation")
    parser.add_argument("--workspace", default=".", help="Working directory")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mode = DeploymentMode(args.mode)
    operation = Operation(args.operation)

    if mode != DeploymentMode.LOCAL:
        raise RuntimeError("Demo currently supports only local mode")

    context = WorkflowContext(mode=mode, workspace_dir=args.workspace)
    service = DeploymentService(LocalCommandExecutor(working_dir=args.workspace))
    result = service.run(operation, context)

    print(f"Operation: {result.operation.value}")
    for item in result.results:
        print(f"[{item.step_id}] {item.label} -> exit={item.exit_code}")
        if item.stdout.strip():
            print(item.stdout.strip())
        if item.stderr.strip():
            print(item.stderr.strip())

    return 1 if result.stopped_early else 0


if __name__ == "__main__":
    raise SystemExit(main())
