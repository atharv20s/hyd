"""Operator-owned scrape jobs and a durable concurrent draft queue."""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, desc
from app.core.database import get_db
from app.core.security import get_operator_user
from app.models.models import Job, Lead, WorkItem
from app.schemas.schemas import JobCreateRequest, JobResponse
from app.services.scraper_service import ScraperService
from app.services.communication_service import is_suppressed

router = APIRouter(tags=["Scraping & Tasks"])


class CSVImport(BaseModel):
    csv_text: str = Field(min_length=1, max_length=2_000_000)


class DraftBatch(BaseModel):
    lead_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    scheduled_at: datetime | None = None


@router.post("/leads/import")
async def import_csv(payload: CSVImport, user=Depends(get_operator_user), db=Depends(get_db)):
    try:
        return {"imported": await ScraperService(db).ingest_csv(user.id, payload.csv_text)}
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get("/jobs", response_model=list[JobResponse])
async def list_jobs(user=Depends(get_operator_user), db=Depends(get_db)):
    return (await db.scalars(select(Job).where(Job.user_id == user.id).order_by(desc(Job.created_at)).limit(100))).all()


@router.post("/jobs", response_model=JobResponse, status_code=202)
async def create_job(payload: JobCreateRequest, user=Depends(get_operator_user), db=Depends(get_db)):
    keywords = [word.strip() for word in payload.keywords if word.strip()]
    if not keywords or len(keywords) > 20 or any(len(word) > 200 for word in keywords):
        raise HTTPException(422, "Provide between 1 and 20 search terms, at most 200 characters each.")
    if payload.prepare_drafts and not user.service_description.strip():
        raise HTTPException(409, "Save your offer in Memory & strategy before preparing outreach automatically.")
    if payload.scheduled_at and payload.scheduled_at.tzinfo is None:
        raise HTTPException(422, "Scheduled time must include a timezone.")
    job = Job(user_id=user.id, keywords=keywords, city=payload.city.strip() or "Shared device location", latitude=payload.latitude,
              longitude=payload.longitude, depth=payload.depth, status="queued",
              location_source=payload.location_source, location_captured_at=payload.location_captured_at,
              radius_m=payload.radius_m, prepare_drafts=payload.prepare_drafts, draft_limit=payload.draft_limit)
    db.add(job)
    await db.flush()
    db.add(WorkItem(user_id=user.id, kind="scrape", payload={"job_id": str(job.id)},
                    available_at=payload.scheduled_at or datetime.now(timezone.utc)))
    await db.commit()
    await db.refresh(job)
    return job


@router.post("/leads/batch-draft", status_code=202)
async def batch_draft(payload: DraftBatch, user=Depends(get_operator_user), db=Depends(get_db)):
    if not user.service_description:
        raise HTTPException(409, "Save your offer and target clients before drafting.")
    if payload.scheduled_at and payload.scheduled_at.tzinfo is None:
        raise HTTPException(422, "Scheduled time must include a timezone.")
    items = []
    for lead_id in set(payload.lead_ids):
        lead = await db.scalar(select(Lead).where(Lead.id == lead_id, Lead.user_id == user.id).with_for_update())
        if not lead:
            raise HTTPException(404, "Lead not found.")
        if lead.status in ("queued", "processing", "contacted", "replied", "opted_out") or await is_suppressed(db, lead):
            continue
        item = WorkItem(user_id=user.id, kind="draft", payload={"lead_id": str(lead_id)},
                        available_at=payload.scheduled_at or datetime.now(timezone.utc))
        db.add(item)
        lead.status = "queued"
        items.append(item)
    await db.commit()
    return {"queued": len(items), "task_ids": [str(item.id) for item in items]}


@router.get("/tasks")
async def tasks(user=Depends(get_operator_user), db=Depends(get_db), limit: int = Query(50, ge=1, le=200)):
    items = (await db.scalars(select(WorkItem).where(WorkItem.user_id == user.id).order_by(desc(WorkItem.created_at)).limit(limit))).all()
    return [{"id": str(item.id), "kind": item.kind, "status": item.status, "error_message": item.error_message,
             "available_at": item.available_at, "completed_at": item.completed_at} for item in items]


@router.post("/tasks/{task_id}/retry", status_code=202)
async def retry_task(task_id: uuid.UUID, user=Depends(get_operator_user), db=Depends(get_db)):
    item = await db.scalar(select(WorkItem).where(WorkItem.id == task_id, WorkItem.user_id == user.id).with_for_update())
    if not item:
        raise HTTPException(404, "Task not found.")
    if item.status != "failed":
        raise HTTPException(409, "Only a failed task can be retried.")
    if item.kind == "draft":
        lead = await db.get(Lead, uuid.UUID(item.payload["lead_id"]))
        if not lead or lead.status in ("contacted", "replied") or await is_suppressed(db, lead):
            raise HTTPException(409, "This business cannot receive new outreach.")
        lead.status = "queued"
    if item.kind == "scrape":
        job = await db.get(Job, uuid.UUID(item.payload["job_id"]))
        job.status = "queued"
        job.error_message = None
    item.status = "pending"
    item.available_at = datetime.now(timezone.utc)
    item.error_message = None
    await db.commit()
    return {"status": "queued"}
