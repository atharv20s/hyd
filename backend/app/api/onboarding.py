"""Operator Onboarding: Accepts the two core inputs (Service Offered, Target Clients)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import get_operator_user as get_current_user
from app.models.models import User
from app.schemas.schemas import OnboardingRequest, UserResponse

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])


@router.post("", response_model=UserResponse)
async def set_onboarding_inputs(
    payload: OnboardingRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Sets what the operator offers and who their target clients are."""
    current_user.service_offered = payload.service_offered.strip()
    current_user.target_clients = payload.target_clients.strip()
    await db.commit()
    await db.refresh(current_user)
    return UserResponse.model_validate(current_user)
