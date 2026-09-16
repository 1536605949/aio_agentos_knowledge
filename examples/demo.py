import asyncio

from api.app import build_service
from governance.models import Principal


async def main():
    service = build_service()
    state = await service.start(
        alarm={"alarm_id": "A-1001", "service": "checkout", "severity": "critical"},
        logs=["upstream timeout while calling payment"],
    )
    await asyncio.sleep(0)
    state = service.get(state.workflow_id)
    print("before approval:", state.model_dump(mode="json"))

    if state.status.value == "waiting_approval":
        state = await service.approve(
            state.workflow_id,
            approved=True,
            principal=Principal(subject="oncall@example", roles={"approver"}),
        )
    print("after approval:", state.model_dump(mode="json"))
    print("spans:", [s.model_dump(mode="json") for s in service.trace(state.workflow_id).spans()])


if __name__ == "__main__":
    asyncio.run(main())
