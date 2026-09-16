import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from api.app import app


@pytest.mark.asyncio
async def test_alarm_idempotency_and_status():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "alarm_id": "API-1",
            "service": "checkout",
            "logs": ["upstream timeout"],
            "idempotency_key": "idem-api-1",
        }
        first = await client.post("/alarm", json=payload)
        second = await client.post("/alarm", json=payload)
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["workflow_id"] == second.json()["workflow_id"]

        workflow_id = first.json()["workflow_id"]
        for _ in range(50):
            status = await client.get(f"/workflow/{workflow_id}")
            if status.json()["status"] == "waiting_approval":
                break
            await asyncio.sleep(0.01)
        assert status.json()["status"] == "waiting_approval"

        approval = await client.post(
            f"/approval/{workflow_id}",
            json={"approved": True, "approver": "oncall"},
        )
        assert approval.status_code == 200
        assert approval.json()["status"] == "completed"

        trace = await client.get(f"/workflow/{workflow_id}/trace")
        assert trace.status_code == 200
        assert any(s["name"] == "tool.invoke" for s in trace.json()["spans"])
