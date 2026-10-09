"""Two-input campaigns with immutable strategy snapshots and pause/resume control."""
import uuid
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select, func
from app.core.database import get_db
from app.core.security import get_operator_user
from app.models.models import Campaign, Job, Lead, WorkItem, User, CampaignLead
from app.schemas.schemas import OnboardingRequest

router = APIRouter(tags=["Campaign Orchestrator"])


class CampaignRequest(OnboardingRequest):
    request_id: uuid.UUID = Field(default_factory=uuid.uuid4)


@router.post("/campaigns", status_code=202)
async def start_campaign(payload: CampaignRequest, user=Depends(get_operator_user), db=Depends(get_db)):
    if not payload.service_description.strip() or not payload.target_clients.strip():
        raise HTTPException(422, "Both answers are required.")
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    existing = await db.scalar(select(Campaign).where(Campaign.user_id == user.id, Campaign.request_id == payload.request_id))
    if existing:
        return {"id": str(existing.id), "status": existing.status}
    user.service_description = payload.service_description.strip()
    user.target_clients = payload.target_clients.strip()
    campaign = Campaign(user_id=user.id, request_id=payload.request_id,
        service_description=user.service_description, target_clients=user.target_clients)
    db.add(campaign)
    await db.flush()
    db.add(WorkItem(user_id=user.id, kind="campaign_plan", payload={"campaign_id": str(campaign.id)}))
    await db.commit()
    return {"id": str(campaign.id), "status": campaign.status}


@router.get("/campaigns")
async def campaigns(user=Depends(get_operator_user), db=Depends(get_db)):
    rows = (await db.scalars(select(Campaign).where(Campaign.user_id == user.id).order_by(Campaign.created_at.desc()).limit(50))).all()
    result = []
    for row in rows:
        jobs = (await db.scalars(select(Job).where(Job.campaign_id == row.id))).all()
        counts = dict((await db.execute(select(Lead.outreach_status, func.count(Lead.id))
            .join(CampaignLead, CampaignLead.lead_id == Lead.id).where(CampaignLead.campaign_id == row.id).group_by(Lead.outreach_status))).all())
        status = row.status
        if jobs and all(job.status == "completed" for job in jobs):
            status = "preparing_ideas" if counts.get("queued", 0) + counts.get("processing", 0) else "ready_for_review"
        if any(job.status == "failed" for job in jobs):
            status = "needs_attention"
        result.append({"id": str(row.id), "service_description": row.service_description,
            "target_clients": row.target_clients, "plan": row.plan, "status": status, "paused": row.paused,
            "counts": counts, "total": sum(counts.values()), "job_ids": [str(job.id) for job in jobs],
            "created_at": row.created_at})
    return result


@router.post("/campaigns/{campaign_id}/{action}")
async def control(campaign_id: uuid.UUID, action: Literal["pause", "resume"],
                  user=Depends(get_operator_user), db=Depends(get_db)):
    row = await db.scalar(select(Campaign).where(Campaign.id == campaign_id, Campaign.user_id == user.id).with_for_update())
    if not row:
        raise HTTPException(404, "Campaign not found.")
    row.paused = action == "pause"
    if not row.paused:
        items = (await db.scalars(select(WorkItem).where(WorkItem.user_id == user.id, WorkItem.status == "pending"))).all()
        for item in items:
            if item.payload.get("campaign_id") == str(row.id):
                item.available_at = datetime.now(timezone.utc)
    await db.commit()
    return {"paused": row.paused}
