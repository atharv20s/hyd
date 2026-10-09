import json
import uuid
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from sqlalchemy import select, func
from app import worker
from app.models.models import Job, Lead, WorkItem, SendLog
from app.services.scraper_service import ScraperService, distance_m
from conftest import signup


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"latitude": "91", "longitude": "78"}, {"latitude": "17", "longitude": "181"},
    {"latitude": "NaN", "longitude": "78"}, {"latitude": "17"},
    {"longitude": "78"}, {"radius_m": 499}, {"radius_m": 50001},
    {"city": ""}, {"location_source": "browser"},
    {"location_source": "browser", "latitude": "17", "longitude": "78",
     "location_captured_at": (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()},
])
async def test_invalid_location_rejected(harness, changes):
    client, _, _ = harness
    _, headers = await signup(client)
    response = await client.post("/api/v1/jobs", headers=headers,
        json={"keywords": ["salon"], "city": "Hyderabad", **changes})
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_browser_search_filters_radius_and_prepares_capped_drafts_once(harness, monkeypatch):
    client, factory, _ = harness
    _, headers = await signup(client)
    await client.post("/api/v1/onboarding", headers=headers,
        json={"service_description": "Receptionist service", "target_clients": "Nearby salons"})
    response = await client.post("/api/v1/jobs", headers=headers, json={
        "keywords": ["salon"], "city": "Do not append this stale area",
        "latitude": 0, "longitude": 0, "location_source": "browser", "radius_m": 1000,
        "location_captured_at": datetime.now(timezone.utc).isoformat(),
        "prepare_drafts": True, "draft_limit": 1,
    })
    assert response.status_code == 202, response.text
    job_id = uuid.UUID(response.json()["id"])
    remote_requests = []
    original_client = httpx.AsyncClient
    def remote(request):
        remote_requests.append(request)
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["keywords"] == ["salon"]
            assert payload["lat"] == "0.0" and payload["lon"] == "0.0"
            assert payload["radius"] == 1000 and payload["email"] is True
            return httpx.Response(201, json={"id": "live-job"})
        if request.url.path.endswith("download"):
            return httpx.Response(200, text=(
                "title,address,latitude,longitude,emails\n"
                "Close Salon,A,0,0,close@example.com\n"
                "Close Salon,A,0,0,close@example.com\n"
                "Second Salon,B,0.001,0.001,second@example.com\n"
                "Far Salon,C,1,1,far@example.com\n"
                "No Coordinates,D,,,unknown@example.com\n"
                "Invalid Coordinates,E,NaN,Infinity,bad@example.com\n"))
        return httpx.Response(200, json={"Status": "ok"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(remote), **kwargs))
    claims = await worker.claim_work(3)
    await worker.process_work(claims[0])
    jobs = (await client.get("/api/v1/jobs", headers=headers)).json()
    assert jobs[0]["status"] == "completed", jobs
    assert jobs[0]["lead_count"] == 2
    assert jobs[0]["excluded_count"] == 3
    assert jobs[0]["drafts_queued"] == 1
    async with factory() as db:
        assert await db.scalar(select(func.count(SendLog.id))) == 0
        assert await db.scalar(select(func.count(WorkItem.id)).where(WorkItem.kind == "draft")) == 1
        job = await db.get(Job, job_id)
        assert await ScraperService(db).poll(job) == "completed"
        assert job.lead_count == 2
        assert await db.scalar(select(func.count(WorkItem.id)).where(WorkItem.kind == "draft")) == 1
    assert len(remote_requests) == 3


@pytest.mark.asyncio
async def test_auto_drafts_require_strategy_and_manual_area_still_works(harness):
    client, _, _ = harness
    _, headers = await signup(client)
    response = await client.post("/api/v1/jobs", headers=headers,
        json={"keywords": ["salon"], "city": "Hyderabad", "prepare_drafts": True})
    assert response.status_code == 409
    response = await client.post("/api/v1/jobs", headers=headers,
        json={"keywords": ["salon"], "city": "Hyderabad"})
    assert response.status_code == 202
    assert response.json()["location_source"] == "manual"


def test_radius_distance_antimeridian_and_origin():
    assert distance_m(0, 0, 0, 0) == 0
    assert 110000 < distance_m(0, 0, 1, 0) < 112000
    assert distance_m(0, 179.999, 0, -179.999) < 250
