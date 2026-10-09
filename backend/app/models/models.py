"""SQLAlchemy models for PostgreSQL — users, leads, jobs, send log."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, ForeignKey, Index, JSON, UniqueConstraint, Uuid as UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    """Operator / service provider who logs in and uses the platform."""
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), default="")
    # The two core inputs
    service_description = Column(Text, default="")  # "What is your service?"
    target_clients = Column(Text, default="")        # "Who are your clients?"
    device_id = Column(String(255), nullable=True)   # anonymous guest cookie → attached at signup
    is_active = Column(Boolean, default=True)
    account_role = Column(String(20), default="operator", nullable=False)
    client_lead_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Relationships
    jobs = relationship("Job", back_populates="user")
    leads = relationship("Lead", back_populates="user")

    @property
    def service_offered(self):
        return self.service_description

    @service_offered.setter
    def service_offered(self, value):
        self.service_description = value


class Lead(Base):
    """A business discovered by the scout agent."""
    __tablename__ = "leads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=True, index=True)

    # From scraper (full fields)
    title = Column(String(500), nullable=False)
    phone = Column(String(100), default="")
    emails = Column(Text, default="")           # comma-separated
    website = Column(String(1000), default="")
    category = Column(String(255), default="")
    address = Column(Text, default="")
    review_rating = Column(Float, nullable=True)
    review_count = Column(Integer, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    place_id = Column(String(255), default="")
    google_maps_url = Column(String(1000), default="")

    # Enrichment
    social_instagram = Column(String(500), default="")
    social_facebook = Column(String(500), default="")
    social_linkedin = Column(String(500), default="")
    business_summary = Column(Text, default="")    # AI-generated summary of what they do
    proposed_idea = Column(Text, default="")        # the pitch idea
    idea_approved = Column(Boolean, nullable=True)  # None=pending, True=approved, False=rejected

    # Outreach state
    outreach_status = Column(
        String(50), default="new",
        comment="new → idea_pending → approved → drafted → sent → replied → deal → closed"
    )
    last_contacted_at = Column(DateTime(timezone=True), nullable=True)
    opted_out = Column(Boolean, default=False)
    pitch_hook = Column(Text, default="")
    draft_subject = Column(String(500), default="")
    draft_body = Column(Text, default="")
    raw_data = Column(JSON, default=dict)

    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Relationships
    user = relationship("User", back_populates="leads")
    send_log = relationship("SendLog", back_populates="lead")

    __table_args__ = (
        Index("ix_leads_user_status", "user_id", "outreach_status"),
    )

    @property
    def email(self):
        return (self.emails or "").split(",")[0].strip() or None

    @email.setter
    def email(self, value):
        self.emails = value or ""

    @property
    def status(self):
        return self.outreach_status

    @status.setter
    def status(self, value):
        self.outreach_status = value

    @property
    def unsubscribed(self):
        return self.opted_out

    @unsubscribed.setter
    def unsubscribed(self, value):
        self.opted_out = value

    @property
    def contacted_at(self):
        return self.last_contacted_at

    @contacted_at.setter
    def contacted_at(self, value):
        self.last_contacted_at = value


class Job(Base):
    """A scraping / enrichment job submitted to the scraper container."""
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("campaigns.id"), nullable=True, index=True)
    scraper_job_id = Column(String(100), nullable=True)  # ID from the gmaps scraper API
    keywords = Column(JSON, nullable=False)               # array of keywords
    city = Column(String(255), default="")
    latitude = Column(String(50), default="")
    longitude = Column(String(50), default="")
    depth = Column(Integer, default=5)
    location_source = Column(String(20), default="manual", nullable=False)
    location_captured_at = Column(DateTime(timezone=True), nullable=True)
    radius_m = Column(Integer, default=5000, nullable=False)
    prepare_drafts = Column(Boolean, default=False, nullable=False)
    draft_limit = Column(Integer, default=10, nullable=False)
    drafts_queued = Column(Integer, default=0, nullable=False)
    excluded_count = Column(Integer, default=0, nullable=False)
    status = Column(String(50), default="pending")        # pending → working → ok → failed
    lead_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

    user = relationship("User", back_populates="jobs")

    @property
    def location(self):
        return self.city

    @location.setter
    def location(self, value):
        self.city = value


class SendLog(Base):
    """Idempotent record of every outreach message sent. Prevents duplicates, enforces caps."""
    __tablename__ = "send_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    channel = Column(String(50), nullable=False)   # "email" | "voice" | "telegram"
    message_type = Column(String(50), default="outreach")  # outreach | follow_up | reply
    subject = Column(String(500), default="")
    body_preview = Column(Text, default="")
    status = Column(String(50), default="sent")    # sent | delivered | bounced | failed
    idempotency_key = Column(String(255), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    recipient = Column(String(500), default="")
    body = Column(Text, default="")
    resend_id = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), default=utcnow)

    lead = relationship("Lead", back_populates="send_log")

    __table_args__ = (
        Index("ix_sendlog_user_date", "user_id", "sent_at"),
    )


class WorkItem(Base):
    """Durable queue shared by all API and worker replicas."""
    __tablename__ = "work_items"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String(30), nullable=False)
    payload = Column(JSON, default=dict)
    status = Column(String(20), default="pending", nullable=False, index=True)
    available_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    leased_until = Column(DateTime(timezone=True), nullable=True)
    attempts = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class ContactSuppression(Base):
    """Durable per-operator opt-out; survives reimports and new leads at the same address."""
    __tablename__ = "contact_suppressions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    channel = Column(String(20), nullable=False, default="email")
    address = Column(String(500), nullable=False)
    reason = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "channel", "address"),)


class Communication(Base):
    __tablename__ = "communications"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=False, index=True)
    client_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    channel = Column(String(20), default="email", nullable=False)
    direction = Column(String(20), default="inbound", nullable=False)
    subject = Column(String(500), default="")
    body = Column(Text, default="")
    classification = Column(String(40), default="needs_review")
    status = Column(String(40), default="received")
    draft_response = Column(Text, default="")
    event_key = Column(String(255), unique=True, nullable=False)
    provider_message_id = Column(String(500), default="")
    scheduled_event_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class ScheduledEvent(Base):
    """An appointment, meeting, or demo scheduled with a lead via email reply or agent."""
    __tablename__ = "scheduled_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=False, index=True)
    communication_id = Column(UUID(as_uuid=True), ForeignKey("communications.id"), nullable=True, index=True)
    title = Column(String(500), nullable=False)
    description = Column(Text, default="")
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(50), default="confirmed", nullable=False)  # confirmed | pending | cancelled
    attendee_email = Column(String(255), nullable=False)
    meeting_link = Column(String(1000), default="")
    location = Column(String(500), default="")
    ics_uid = Column(String(255), nullable=True)
    source = Column(String(50), default="resend_communication_agent")
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    lead = relationship("Lead")
    user = relationship("User")


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider = Column(String(30), nullable=False)
    event_key = Column(String(255), unique=True, nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(String(30), default="queued")
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Campaign(Base):
    __tablename__ = "campaigns"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    request_id = Column(UUID(as_uuid=True), nullable=False)
    service_description = Column(Text, nullable=False)
    target_clients = Column(Text, nullable=False)
    plan = Column(JSON, default=dict)
    status = Column(String(30), default="planning", nullable=False)
    paused = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "request_id"),)


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=True)
    event_key = Column(String(255), unique=True, nullable=False)
    title = Column(String(255), nullable=False)
    body = Column(Text, default="")
    read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class CampaignLead(Base):
    __tablename__ = "campaign_leads"
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("campaigns.id"), primary_key=True)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), primary_key=True)


class Meeting(Base):
    __tablename__ = "meetings"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=False)
    message_id = Column(UUID(as_uuid=True), ForeignKey("communications.id"), nullable=False, unique=True)
    title = Column(String(255), nullable=False)
    starts_at = Column(DateTime(timezone=True), nullable=True)
    duration_minutes = Column(Integer, default=30, nullable=False)
    location = Column(String(1000), default="")
    source_quote = Column(Text, default="")
    status = Column(String(30), default="proposed", nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Followup(Base):
    __tablename__ = "followups"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=False)
    due_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(30), default="scheduled", nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
