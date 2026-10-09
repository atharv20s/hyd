"""Pydantic schemas for API request / response validation."""

from datetime import datetime, timezone
from typing import Optional, List, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


# ── Auth ──

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    full_name: str = Field(default="", max_length=255)
    device_id: Optional[str] = None
    invite_token: Optional[str] = None

    @field_validator("password")
    @classmethod
    def password_bytes(cls, value):
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes.")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: "UserResponse"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    service_description: str
    target_clients: str
    created_at: datetime
    account_role: str = "operator"
    client_lead_id: Optional[UUID] = None

    model_config = {"from_attributes": True}


# ── Onboarding (the 2 inputs) ──

class OnboardingRequest(BaseModel):
    service_description: str = Field(
        ..., min_length=1, max_length=4000, description="What is your service? e.g. 'AI receptionist for clinics'"
    )
    target_clients: str = Field(
        ..., min_length=1, max_length=4000, description="Who are your clients? e.g. 'dental clinics in Hyderabad'"
    )

    @property
    def service_offered(self) -> str:
        return self.service_description


# ── Jobs ──

class JobCreateRequest(BaseModel):
    keywords: List[str] = Field(..., min_length=1)
    city: str = Field(default="", max_length=255)
    latitude: str = ""
    longitude: str = ""
    depth: int = Field(default=5, ge=1, le=10)
    scheduled_at: Optional[datetime] = None
    location_source: Literal["manual", "browser"] = "manual"
    location_captured_at: Optional[datetime] = None
    radius_m: int = Field(default=5000, ge=500, le=50000)
    prepare_drafts: bool = False
    draft_limit: int = Field(default=10, ge=1, le=100)

    @field_validator("latitude", "longitude", mode="before")
    @classmethod
    def coordinate(cls, value, info):
        import math
        if value is None or value == "":
            return ""
        number = float(value)
        limit = 90 if info.field_name == "latitude" else 180
        if not math.isfinite(number) or not -limit <= number <= limit:
            raise ValueError(f"{info.field_name} is outside its valid range")
        return str(number)

    @model_validator(mode="after")
    def location(self):
        if bool(self.latitude) != bool(self.longitude):
            raise ValueError("Both latitude and longitude are required together.")
        if not self.city.strip() and not self.latitude:
            raise ValueError("Enter an area or share your location.")
        if self.location_source == "browser":
            if not self.latitude or not self.location_captured_at or not self.location_captured_at.tzinfo:
                raise ValueError("Browser location requires coordinates and a timezone-aware capture time.")
            age = (datetime.now(timezone.utc) - self.location_captured_at).total_seconds()
            if not -60 <= age <= 300:
                raise ValueError("Location is older than 5 minutes. Use my location again before submitting.")
        return self


class JobResponse(BaseModel):
    id: UUID
    scraper_job_id: Optional[str]
    keywords: List[str]
    city: str
    status: str
    lead_count: int
    created_at: datetime
    completed_at: Optional[datetime]
    error_message: Optional[str] = None
    location_source: str = "manual"
    radius_m: int = 5000
    latitude: str = ""
    longitude: str = ""
    prepare_drafts: bool = False
    drafts_queued: int = 0
    excluded_count: int = 0

    model_config = {"from_attributes": True}

    @field_validator("keywords", mode="before")
    @classmethod
    def decode_keywords(cls, value):
        import json
        return json.loads(value) if isinstance(value, str) else value


# ── Leads ──

class LeadResponse(BaseModel):
    id: UUID
    title: str
    phone: str
    emails: str
    website: str
    category: str
    address: str
    review_rating: Optional[float]
    review_count: Optional[int]
    proposed_idea: str
    idea_approved: Optional[bool]
    outreach_status: str
    created_at: datetime
    pitch_hook: str = ""
    draft_subject: str = ""
    draft_body: str = ""

    model_config = {"from_attributes": True}


class DecisionRequest(BaseModel):
    action: str = Field(pattern="^(approved|edited|rejected)$")
    edited_subject: Optional[str] = None
    edited_body: Optional[str] = None
    feedback_tag: Optional[str] = None
    notes: Optional[str] = None
    send_email: bool = False


class DecisionResponse(BaseModel):
    decision_id: str
    action: str
    lead_id: UUID
    status: str
    message: str


class LeadIdeaApproval(BaseModel):
    approved: bool
    edited_idea: str = ""  # if the human edited the idea
    reason: str = ""       # optional tag for learning


class LeadDraftApproval(BaseModel):
    approved: bool
    edited_body: str = ""
    reason: str = ""


# ── Dashboard stats ──

class DashboardStats(BaseModel):
    total_leads: int
    leads_with_email: int
    leads_with_phone: int
    ideas_pending: int
    messages_sent_today: int
    replies_received: int
