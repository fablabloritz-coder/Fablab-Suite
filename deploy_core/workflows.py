from typing import List

from deploy_core.models import DeploymentMode, Operation, StepSpec, WorkflowContext


def _helper_cmd(ctx: WorkflowContext, action: str, app_name: str = "") -> str:
    env_arg = f" --env-file {ctx.env_file}" if ctx.env_file else ""
    app_arg = f" {app_name}" if app_name else ""
    base = f"cd {ctx.remote_dir} ; ./{ctx.helper_script} {action}{app_arg}{env_arg}"
    return base


def _build_local_workflow(op: Operation, ctx: WorkflowContext) -> List[StepSpec]:
    if op in (Operation.INSTALL, Operation.UPDATE):
        return [
            StepSpec("local-up", "Local compose up", "docker compose up -d --build"),
        ]
    if op == Operation.AUDIT:
        return [
            StepSpec("local-ps", "Local compose ps", "docker compose ps"),
            StepSpec("local-list", "Docker compose projects", "docker compose ls", stop_on_failure=False),
        ]
    if op == Operation.STATUS:
        return [
            StepSpec("local-status", "Local status", "docker compose ps"),
        ]
    if op == Operation.LOGS:
        return [
            StepSpec("local-logs", "Local logs", "docker compose logs --tail=300"),
        ]
    raise ValueError(f"Unsupported operation for local mode: {op}")


def _build_ssh_workflow(op: Operation, ctx: WorkflowContext) -> List[StepSpec]:
    if op == Operation.INSTALL:
        return [
            StepSpec("repair-env", "Repair env", _helper_cmd(ctx, "repair-env")),
            StepSpec("data-safety", "Data safety precheck", _helper_cmd(ctx, "check-data-safety")),
            StepSpec("install", "Install suite", _helper_cmd(ctx, "install")),
        ]
    if op == Operation.UPDATE:
        return [
            StepSpec("repair-env", "Repair env", _helper_cmd(ctx, "repair-env")),
            StepSpec("data-safety", "Data safety precheck", _helper_cmd(ctx, "check-data-safety")),
            StepSpec("update", "Update suite", _helper_cmd(ctx, "update")),
        ]
    if op == Operation.AUDIT:
        return [
            StepSpec("audit", "Audit server", _helper_cmd(ctx, "audit")),
            StepSpec("data-safety", "Data safety post-audit", _helper_cmd(ctx, "check-data-safety"), stop_on_failure=False),
        ]
    if op == Operation.STATUS:
        return [
            StepSpec("status", "Suite status", _helper_cmd(ctx, "status")),
        ]
    if op == Operation.LOGS:
        return [
            StepSpec("logs", "Suite logs", _helper_cmd(ctx, "logs", ctx.logs_app)),
        ]
    raise ValueError(f"Unsupported operation for ssh mode: {op}")


def build_workflow(op: Operation, ctx: WorkflowContext) -> List[StepSpec]:
    if ctx.mode == DeploymentMode.LOCAL:
        return _build_local_workflow(op, ctx)
    if ctx.mode == DeploymentMode.SSH:
        return _build_ssh_workflow(op, ctx)
    raise ValueError(f"Unsupported deployment mode: {ctx.mode}")
