"""Human-in-the-loop decisions and learned rules inspection API."""

from typing import List, Dict, Any
from fastapi import APIRouter, Depends, Query, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.core.database import get_mongo_db
from app.core.security import get_operator_user as get_current_user
from app.models.models import User
from app.services.memory_service import MemoryService

router = APIRouter(prefix="/decisions", tags=["Human Memory & Learning"])


class StyleRuleRequest(BaseModel):
    rule: str = Field(min_length=1, max_length=1000)


@router.get("")
async def get_human_decisions(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    mongo: AsyncIOMotorDatabase = Depends(get_mongo_db),
):
    """Fetches past human checkpoint actions (approvals, edits, rejections) stored in MongoDB."""
    memory_svc = MemoryService(mongo)
    return await memory_svc.get_recent_decisions(str(current_user.id), limit=limit)


@router.get("/rules")
async def get_learned_rules(
    current_user: User = Depends(get_current_user),
    mongo: AsyncIOMotorDatabase = Depends(get_mongo_db),
):
    """Fetches accumulated operator style rules injected into agent prompts."""
    memory_svc = MemoryService(mongo)
    rules = await memory_svc.get_operator_rules(str(current_user.id))
    return {"operator_id": str(current_user.id), "rules": rules}


@router.post("/rules")
async def add_style_rule(
    payload: StyleRuleRequest,
    current_user: User = Depends(get_current_user),
    mongo: AsyncIOMotorDatabase = Depends(get_mongo_db),
):
    """Explicitly adds an operator style rule to memory."""
    memory_svc = MemoryService(mongo)
    if not payload.rule.strip():
        raise HTTPException(422, "A non-empty preference is required.")
    await memory_svc._update_operator_rule(str(current_user.id), payload.rule.strip())
    return {"status": "added", "rule": payload.rule.strip()}
