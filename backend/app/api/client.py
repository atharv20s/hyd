"""Authenticated client portal; the invitation fixes the client-to-business binding."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app.core.database import get_db, get_mongo_db
from app.core.security import get_current_user, get_operator_user
from app.models.models import Lead
from app.services.memory_service import MemoryService
from app.services.assistant_service import client_reply

router = APIRouter(tags=["Client Assistant"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class IntakeRequest(BaseModel):
    service: str = Field(min_length=1, max_length=1000)
    location: str = Field(min_length=1, max_length=1000)
    preferred_at: datetime
    contact: str = Field(min_length=1, max_length=500)


async def client_lead(user=Depends(get_current_user), db=Depends(get_db)):
    if user.account_role != "client" or not user.client_lead_id:
        raise HTTPException(403, "A client invitation and account are required.")
    lead = await db.get(Lead, user.client_lead_id)
    if not lead:
        raise HTTPException(404, "This business is no longer available.")
    return lead


@router.get("/client/profile")
async def profile(lead=Depends(client_lead)):
    return {"business_name": lead.title, "category": lead.category, "address": lead.address,
            "phone": lead.phone, "summary": lead.business_summary, "lead_id": str(lead.id)}


@router.get("/client/history")
async def history(user=Depends(get_current_user), lead=Depends(client_lead), mongo=Depends(get_mongo_db)):
    return await MemoryService(mongo).get_chat_history(f"client:{user.id}:{lead.id}", 50)


@router.post("/client/chat")
async def chat(payload: ChatRequest, user=Depends(get_current_user), lead=Depends(client_lead), mongo=Depends(get_mongo_db), db=Depends(get_db)):
    import uuid
    from app.models.models import Communication
    from app.services.communication_service import publish
    from app.services.communication_service import stop_reason, suppress_contact
    reason = stop_reason(payload.message)
    if reason:
        await suppress_contact(db, lead, reason)
        reply = "Understood. No further outreach will be sent to this contact."
        memory = MemoryService(mongo)
        metadata = {"operator_id": str(lead.user_id), "lead_id": str(lead.id), "client_id": str(user.id)}
        await memory.save_chat_message(f"client:{user.id}:{lead.id}", "client", "user", payload.message, metadata)
        await memory.save_chat_message(f"client:{user.id}:{lead.id}", "agent", "assistant", reply, metadata)
    else:
        reply = await client_reply(MemoryService(mongo), user, lead, payload.message)
    item = Communication(user_id=lead.user_id, lead_id=lead.id, client_user_id=user.id, channel="portal",
        direction="inbound", subject=f"Client chat · {user.full_name or 'Client'}", body=payload.message,
        classification=reason or "client_chat", status="suppressed" if reason else "needs_review", event_key=f"portal:{uuid.uuid4()}")
    db.add(item)
    await db.commit()
    await publish(str(lead.user_id), "client_chat", str(item.id))
    return {"reply": reply}


@router.post("/client/intake", status_code=201)
async def intake(payload: IntakeRequest, user=Depends(get_current_user), lead=Depends(client_lead), mongo=Depends(get_mongo_db)):
    if payload.preferred_at.tzinfo is None or payload.preferred_at <= datetime.now(timezone.utc):
        raise HTTPException(422, "Provide a future appointment time including timezone.")
    doc = {"client_id": str(user.id), "lead_id": str(lead.id), "operator_id": str(lead.user_id),
           "business_name": lead.title, **payload.model_dump(), "status": "pending_confirmation", "created_at": datetime.now(timezone.utc)}
    result = await mongo["intake_requests"].insert_one(doc)
    from app.services.communication_service import publish
    await publish(str(lead.user_id), "intake_received", str(result.inserted_id))
    return {"request_id": str(result.inserted_id), "status": "pending_confirmation", "message": "Request saved. The operator will confirm availability."}


@router.get("/client-requests")
async def inbox(user=Depends(get_operator_user), mongo=Depends(get_mongo_db)):
    docs = await mongo["intake_requests"].find({"operator_id": str(user.id)}).sort("created_at", -1).limit(100).to_list(length=100)
    for doc in docs:
        doc["_id"] = str(doc["_id"])
    return docs


class IntakeDecision(BaseModel):
    status: str = Field(pattern="^(confirmed|declined)$")


@router.post("/client-requests/{request_id}/decision")
async def decide_intake(request_id: str, payload: IntakeDecision, user=Depends(get_operator_user), mongo=Depends(get_mongo_db)):
    from bson import ObjectId
    if not ObjectId.is_valid(request_id):
        raise HTTPException(404, "Request not found.")
    result = await mongo["intake_requests"].update_one({"_id": ObjectId(request_id), "operator_id": str(user.id)},
        {"$set": {"status": payload.status, "decided_at": datetime.now(timezone.utc)}})
    if not result.matched_count:
        raise HTTPException(404, "Request not found.")
    return {"status": payload.status}


@router.get("/client/requests")
async def own_requests(user=Depends(get_current_user), lead=Depends(client_lead), mongo=Depends(get_mongo_db)):
    docs = await mongo["intake_requests"].find({"client_id": str(user.id), "lead_id": str(lead.id)}).sort("created_at", -1).limit(50).to_list(length=50)
    for doc in docs:
        doc["_id"] = str(doc["_id"])
    return docs
