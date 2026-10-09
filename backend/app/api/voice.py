"""Authenticated realtime voice sessions and signed transcript ingestion."""
import uuid
import json
import hmac
import hashlib
import time
import base64
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from app.core.database import get_db, get_mongo_db
from app.core.security import get_current_user, get_operator_user
from app.core.config import get_settings
from app.models.models import Lead
from app.services.elevenlabs_service import ElevenLabsService
from app.services.memory_service import MemoryService
from app.services.assistant_service import operator_reply, pipeline_context

router = APIRouter(prefix="/voice", tags=["Voice Assistants"])
settings = get_settings()


class CallAssistantRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    synthesize_audio: bool = False
    voice_id: str = "21m00Tcm4TlvDq8ikWAM"


class VoiceSessionRequest(BaseModel):
    lead_id: uuid.UUID | None = None


class VoiceMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=10000)
    event_id: str = Field(min_length=1, max_length=100)


@router.get("/agents/status")
async def agent_status(user=Depends(get_operator_user)):
    service = ElevenLabsService()
    agents = {}
    for role, agent_id in (("operator", settings.elevenlabs_operator_agent_id), ("client", settings.elevenlabs_agent_id)):
        if not agent_id:
            agents[role] = {"configured": False}
            continue
        try:
            data = await service.get_agent(agent_id)
            agents[role] = {"configured": True, "agent_id": agent_id, "name": data.get("name")}
        except RuntimeError:
            agents[role] = {"configured": False, "error": "Voice provider unavailable."}
    return {"agents": agents}


@router.post("/session")
async def voice_session(payload: VoiceSessionRequest, user=Depends(get_current_user), db=Depends(get_db), mongo=Depends(get_mongo_db)):
    lead = None
    if user.account_role == "client":
        lead = await db.get(Lead, user.client_lead_id) if user.client_lead_id else None
        if payload.lead_id and payload.lead_id != user.client_lead_id:
            raise HTTPException(404, "Business not found.")
        if not lead:
            raise HTTPException(404, "Business not found.")
    elif payload.lead_id:
        lead = await db.get(Lead, payload.lead_id)
        if not lead or lead.user_id != user.id:
            raise HTTPException(404, "Business not found.")
    role = "client" if lead else "operator"
    agent_id = settings.elevenlabs_agent_id if lead else settings.elevenlabs_operator_agent_id
    if not agent_id:
        raise HTTPException(503, f"The {role} voice agent is not configured.")
    if lead:
        dynamic_variables = {"business_name": lead.title, "business_context": json.dumps({
            "category": lead.category, "address": lead.address, "summary": lead.business_summary,
            "data": lead.raw_data or {}
        }), "operator_context": ""}
    else:
        dynamic_variables = {"business_name": "HYD PS2", "business_context": "",
            "operator_context": json.dumps(await pipeline_context(db, user.id))}
    memory_session = f"client:{user.id}:{lead.id}" if lead else f"operator:{user.id}"
    dynamic_variables["conversation_memory"] = json.dumps(await MemoryService(mongo).get_chat_history(memory_session, 10), default=str)
    dynamic_variables["current_time"] = datetime.now(timezone.utc).isoformat()
    service = ElevenLabsService()
    token = await service.get_conversation_token(agent_id)
    conversation_id = token.get("conversation_id")
    if not conversation_id:
        raise HTTPException(502, "Voice provider did not return a conversation ID.")
    await mongo["voice_sessions"].update_one({"_id": conversation_id}, {"$set": {
        "user_id": str(user.id), "operator_id": str(lead.user_id if lead else user.id),
        "lead_id": str(lead.id) if lead else None, "role": role, "created_at": datetime.now(timezone.utc)
    }}, upsert=True)
    return {"conversation_token": token["token"], "conversation_id": conversation_id,
            "dynamic_variables": dynamic_variables, "role": role, "user_id": str(user.id)}


@router.get("/demo-widget/{lead_id}")
async def demo_widget(lead_id: uuid.UUID, user=Depends(get_operator_user), db=Depends(get_db)):
    lead = await db.get(Lead, lead_id)
    if not lead or lead.user_id != user.id:
        raise HTTPException(404, "Lead not found.")
    return {"lead_id": str(lead.id), "business_name": lead.title,
            "config": await ElevenLabsService().get_demo_widget_config(lead.title, lead.pitch_hook or "Appointment intake")}


@router.post("/operator-call-assistant")
async def assistant(payload: CallAssistantRequest, user=Depends(get_operator_user), db=Depends(get_db), mongo=Depends(get_mongo_db)):
    reply = await operator_reply(db, MemoryService(mongo), user, payload.message)
    audio = await ElevenLabsService().generate_speech_preview(reply, payload.voice_id) if payload.synthesize_audio else None
    return {"reply": reply, "has_audio": audio is not None,
            "audio_base64": base64.b64encode(audio).decode() if audio else None}


@router.get("/operator-history")
async def history(user=Depends(get_operator_user), mongo=Depends(get_mongo_db)):
    return await MemoryService(mongo).get_chat_history(f"operator:{user.id}", 50)


@router.get("/tools/pipeline")
async def pipeline(location: str = "", user=Depends(get_operator_user), db=Depends(get_db)):
    return await pipeline_context(db, user.id, location[:200])


@router.post("/sessions/{conversation_id}/messages")
async def save_voice_message(conversation_id: str, payload: VoiceMessage, user=Depends(get_current_user), mongo=Depends(get_mongo_db), db=Depends(get_db)):
    session = await mongo["voice_sessions"].find_one({"_id": conversation_id, "user_id": str(user.id)})
    if not session:
        raise HTTPException(404, "Voice session not found.")
    if user.account_role == "client" and payload.role == "user":
        from app.services.communication_service import stop_reason, suppress_contact
        reason = stop_reason(payload.content)
        if reason:
            lead = await db.get(Lead, user.client_lead_id)
            if lead:
                await suppress_contact(db, lead, reason)
    session_id = f"client:{user.id}:{session['lead_id']}" if session["role"] == "client" else f"operator:{user.id}"
    await mongo["chat_transcripts"].update_one({"_id": f"voice:{conversation_id}:{payload.event_id}"}, {"$setOnInsert": {
        "session_id": session_id, "sender_type": session["role"], "role": payload.role, "content": payload.content,
        "metadata": {"conversation_id": conversation_id, "operator_id": session["operator_id"], "lead_id": session["lead_id"]},
        "timestamp": datetime.now(timezone.utc), "recorded_at_ns": time.time_ns()
    }}, upsert=True)
    return {"saved": True}


@router.post("/webhook")
async def transcript_webhook(request: Request, mongo=Depends(get_mongo_db)):
    secret = settings.elevenlabs_webhook_secret
    if not secret:
        raise HTTPException(503, "Voice webhook signing secret is not configured.")
    body = await request.body()
    parts = dict(piece.split("=", 1) for piece in request.headers.get("elevenlabs-signature", "").split(",") if "=" in piece)
    timestamp = parts.get("t", "")
    try:
        if abs(time.time() - int(timestamp)) > 300:
            raise ValueError()
    except ValueError:
        raise HTTPException(401, "Invalid webhook timestamp.")
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, parts.get("v0", "")):
        raise HTTPException(401, "Invalid webhook signature.")
    payload = json.loads(body)
    data = payload.get("data", payload)
    conversation_id = data.get("conversation_id")
    session = await mongo["voice_sessions"].find_one({"_id": conversation_id})
    if not session:
        return {"status": "ignored", "reason": "Unknown voice session."}
    await mongo["voice_transcripts"].update_one({"_id": conversation_id},
        {"$set": {"transcript": data.get("transcript", []), "session": session, "updated_at": datetime.now(timezone.utc)}}, upsert=True)
    return {"status": "saved"}
