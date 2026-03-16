from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class DeploymentMode(str, Enum):
    LOCAL = "local"
    SSH = "ssh"


class Operation(str, Enum):
    INSTALL = "install"
    UPDATE = "update"
    RESTART = "restart"
    AUDIT = "audit"
    STATUS = "status"
    LOGS = "logs"


@dataclass
class WorkflowContext:
    mode: DeploymentMode
    workspace_dir: str = "."
    remote_dir: str = "~/fabsuite-installer"
    helper_script: str = "fabsuite-ubuntu.sh"
    env_file: Optional[str] = None
    logs_app: str = "Fabtrack"
    extras: Dict[str, str] = field(default_factory=dict)


@dataclass
class StepSpec:
    step_id: str
    label: str
    command: str
    stop_on_failure: bool = True


@dataclass
class CommandResult:
    step_id: str
    label: str
    command: str
    exit_code: int
    stdout: str
    stderr: str


@dataclass
class WorkflowResult:
    operation: Operation
    results: List[CommandResult]
    stopped_early: bool = False
