"""Lead management, orchestrator pipelines, human checkpoints, and opt-out routes."""

import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.database import get_db, get_mongo_db
from app.core.security import get_operator_user as get_current_user, create_invitation
from app.core.config import get_settings
from app.models.models import User, Lead, Job, CampaignLead
from app.schemas.schemas import LeadResponse, DecisionRequest, DecisionResponse
from app.agents.orchestrator import Orchestrator
from app.services.memory_service import MemoryService
from app.services.scraper_service import ScraperService
from app.services.communication_service import suppress_contact, is_suppressed

router = APIRouter(prefix="/leads", tags=["Leads & Outreach"])


@router.get("", response_model=List[LeadResponse])
async def list_leads(
    status_filter: Optional[str] = Query(None, alias="status"),
    has_email: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    campaign_id: Optional[uuid.UUID] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Lead).where(Lead.user_id == current_user.id)
    if campaign_id:
        query = query.join(CampaignLead, CampaignLead.lead_id == Lead.id).where(CampaignLead.campaign_id == campaign_id)
    if status_filter:
        query = query.where(Lead.outreach_status == status_filter)
    if has_email is True:
        query = query.where(Lead.emails != "")
    elif has_email is False:
        query = query.where(Lead.emails == "")

    query = query.order_by(desc(Lead.created_at)).offset(offset).limit(limit)
    result = await db.execute(query)
    leads = result.scalars().all()
    return [LeadResponse.model_validate(l) for l in leads]


@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    lead = await db.get(Lead, lead_id)
    if not lead or lead.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Lead not found.")
    return LeadResponse.model_validate(lead)


@router.post("/{lead_id}/pipeline")
async def trigger_pipeline_for_lead(
    lead_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    mongo: AsyncIOMotorDatabase = Depends(get_mongo_db),
):
    """Runs Scout -> Critic -> Draft and pauses at human checkpoint."""
    lead = await db.scalar(select(Lead).where(Lead.id == lead_id).with_for_update())
    if not lead or lead.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Lead not found.")
    if lead.status in ("queued", "processing", "contacted", "replied", "opted_out") or await is_suppressed(db, lead):
        raise HTTPException(409, "This lead is queued, processing, contacted or opted out.")

    memory_svc = MemoryService(mongo)
    orchestrator = Orchestrator(db, memory_svc)
    state = await orchestrator.run_pipeline_to_checkpoint(current_user, lead)

    return {
        "lead_id": str(lead.id),
        "status": lead.status,
        "selected_pitch": lead.pitch_hook,
        "draft_subject": lead.draft_subject,
        "draft_body": lead.draft_body,
        "awaiting_approval": state["awaiting_human_approval"],
    }


@router.post("/{lead_id}/decision", response_model=DecisionResponse)
async def submit_human_decision(
    lead_id: uuid.UUID,
    payload: DecisionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    mongo: AsyncIOMotorDatabase = Depends(get_mongo_db),
):
    """Operator human checkpoint: approve, edit, or reject the outreach proposal."""
    lead = await db.scalar(select(Lead).where(Lead.id == lead_id).with_for_update())
    if not lead or lead.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Lead not found.")
    if lead.status not in ("drafted", "approved") or not lead.draft_body:
        raise HTTPException(409, "Create a draft before making an approval decision.")

    memory_svc = MemoryService(mongo)
    orchestrator = Orchestrator(db, memory_svc)

    result = await orchestrator.execute_human_decision(
        operator=current_user,
        lead=lead,
        action=payload.action,
        final_subject=payload.edited_subject,
        final_body=payload.edited_body,
        feedback_tag=payload.feedback_tag,
        notes=payload.notes,
        send_email=payload.send_email,
    )

    return DecisionResponse(
        decision_id=result["decision_id"],
        action=result["action"],
        lead_id=lead.id,
        status="contacted" if result["sent"] else lead.status,
        message=result["message"],
    )


@router.post("/{lead_id}/invite")
async def invite_client(lead_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    lead = await db.get(Lead, lead_id)
    if not lead or lead.user_id != user.id:
        raise HTTPException(404, "Lead not found.")
    token = create_invitation(lead.id, user.id)
    return {"url": f"{get_settings().frontend_url.rstrip('/')}/?invite={token}", "business_name": lead.title}


@router.get("/{lead_id}/opt-out", response_class=HTMLResponse)
@router.post("/{lead_id}/opt-out", response_class=HTMLResponse)
async def opt_out_recipient(
    lead_id: uuid.UUID,
    token: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Public one-click unsubscribe endpoint for email recipients (anti-spam law compliance)."""
    import hmac, hashlib
    expected = hmac.new(get_settings().jwt_secret.encode(), f"optout:{lead_id}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(token, expected):
        raise HTTPException(401, "Invalid unsubscribe link.")
    lead = await db.get(Lead, lead_id)
    if lead:
        await suppress_contact(db, lead, "opted_out")

    return """
    <html>
        <head><title>Unsubscribed</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:50px;background:#f9fafb;">
            <div style="background:#fff;max-width:500px;margin:auto;padding:30px;border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,0.05);">
                <h2 style="color:#111;">You have been unsubscribed</h2>
                <p style="color:#666;">You will not receive any further automated outreach messages from this sender.</p>
            </div>
        </body>
    </html>
    """
