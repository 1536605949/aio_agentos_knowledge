from enum import Enum


class AgentLifecycle(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"


_ALLOWED = {
    AgentLifecycle.CREATED: {AgentLifecycle.RUNNING, AgentLifecycle.FAILED},
    AgentLifecycle.RUNNING: {AgentLifecycle.WAITING, AgentLifecycle.COMPLETED, AgentLifecycle.FAILED},
    AgentLifecycle.WAITING: {AgentLifecycle.RUNNING, AgentLifecycle.FAILED},
    AgentLifecycle.COMPLETED: set(),
    AgentLifecycle.FAILED: set(),
}


def transition(current: AgentLifecycle, target: AgentLifecycle) -> AgentLifecycle:
    if target not in _ALLOWED[current]:
        raise ValueError(f"invalid lifecycle transition: {current.value} -> {target.value}")
    return target
