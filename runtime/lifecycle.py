from enum import StrEnum


class AgentLifecycle(StrEnum):
    """单次 Agent 调用的生命周期。

    迁移表是显式的：非法迁移抛 :class:`ValueError`，而不是静默改写状态。
    注意 ``COMPLETED`` / ``FAILED`` 是终态，不可再迁移。
    """

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
