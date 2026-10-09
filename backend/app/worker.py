"""Run python -m app.worker; multiple replicas share a durable SQL queue."""
import asyncio
import logging
import signal
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, or_, and_
from app.core.config import get_settings
from app.core.database import async_session, create_tables, get_mongo_db, close_connections
from app.models.models import WorkItem, Lead, User, Job, WebhookEvent, Campaign, Followup
from app.agents.orchestrator import Orchestrator
from app.services.memory_service import MemoryService
from app.services.scraper_service import ScraperService
from app.services.communication_service import is_suppressed, publish
from app.services.webhook_service import process_resend_event, process_textbee_event

logger = logging.getLogger(__name__)
settings = get_settings()
stopping = asyncio.Event()


async def claim_work(limit):
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        items = (await db.scalars(select(WorkItem).where(or_(
            and_(WorkItem.status == "pending", WorkItem.available_at <= now),
            and_(WorkItem.status == "running", WorkItem.leased_until < now)
        )).order_by(WorkItem.available_at).limit(limit).with_for_update(skip_locked=True))).all()
        claims = []
        for item in items:
            item.status = "running"
            item.leased_until = now + timedelta(minutes=5)
            item.attempts += 1
            claims.append(item.id)
        await db.commit()
        return claims


async def process_work(item_id):
    async with async_session() as db:
        item = await db.get(WorkItem, item_id)
        try:
            campaign = await db.get(Campaign, uuid.UUID(item.payload["campaign_id"])) if item.payload.get("campaign_id") else None
            if campaign and campaign.paused:
                item.status = "pending"
                item.available_at = datetime.now(timezone.utc) + timedelta(seconds=15)
                item.leased_until = None
                await db.commit()
                return
            if item.kind == "campaign_plan":
                from app.agents.campaign_agent import plan_campaign
                if not campaign or campaign.user_id != item.user_id:
                    raise ValueError("Campaign not found")
                await plan_campaign(db, campaign)
            elif item.kind == "draft":
                lead = await db.get(Lead, uuid.UUID(item.payload["lead_id"]))
                user = await db.get(User, item.user_id)
                if not lead or not user or lead.user_id != user.id:
                    raise ValueError("The requested lead no longer exists.")
                if not await is_suppressed(db, lead) and lead.status not in ("contacted", "replied"):
                    lead.status = "processing"
                    await db.commit()
                    await Orchestrator(db, MemoryService(await get_mongo_db())).run_pipeline_to_checkpoint(user, lead)
                    await publish(str(user.id), "draft_ready", str(lead.id))
            elif item.kind == "scrape":
                job = await db.get(Job, uuid.UUID(item.payload["job_id"]))
                if not job or job.user_id != item.user_id:
                    raise ValueError("The requested scrape job no longer exists.")
                scraper = ScraperService(db)
                if not job.scraper_job_id:
                    await scraper.dispatch(job)
                state = await scraper.poll(job)
                if state == "failed":
                    raise ValueError(job.error_message)
                if state != "completed":
                    item.status = "pending"
                    item.available_at = datetime.now(timezone.utc) + timedelta(seconds=15)
                    item.leased_until = None
                    await db.commit()
                    return
            elif item.kind == "resend_event":
                event = await db.get(WebhookEvent, uuid.UUID(item.payload["event_id"]))
                if not event:
                    raise ValueError("Webhook event no longer exists")
                await process_resend_event(db, await get_mongo_db(), event)
            elif item.kind == "textbee_event":
                event = await db.get(WebhookEvent, uuid.UUID(item.payload["event_id"]))
                if not event:
                    raise ValueError("Webhook event no longer exists")
                await process_textbee_event(db, await get_mongo_db(), event)
            elif item.kind == "followup":
                from app.services.scheduling_service import prepare_followup
                followup = await db.get(Followup, uuid.UUID(item.payload["followup_id"]))
                if not followup or followup.user_id != item.user_id:
                    raise ValueError("Follow-up not found")
                await prepare_followup(db, await get_mongo_db(), followup)
            elif item.kind == "operator_alert":
                from app.services.telegram_service import send_operator_alert
                await send_operator_alert(item.payload["title"])
            elif item.kind == "outreach":
                from html import escape
                from app.services.email_service import EmailService
                lead = await db.get(Lead, uuid.UUID(item.payload["lead_id"]))
                if not lead or lead.user_id != item.user_id:
                    raise ValueError("Business not found")
                if lead.status != "approved" or not lead.draft_body.strip() or await is_suppressed(db, lead):
                    raise ValueError("Outreach is no longer approved or contact is suppressed")
                ok, detail = await EmailService(db).send_outreach_email(item.user_id, lead,
                    lead.draft_subject, escape(lead.draft_body).replace(chr(10), "<br>"), lead.draft_body)
                if not ok:
                    raise ValueError("Outreach was not sent; inspect the send ledger and campaign configuration")
            else:
                raise ValueError("Unknown task type.")
            item.status = "completed"
            item.completed_at = datetime.now(timezone.utc)
            item.leased_until = None
            item.error_message = None
            await db.commit()
        except Exception as exc:
            await db.rollback()
            item = await db.get(WorkItem, item_id)
            retry_webhook = item.kind == "resend_event" and item.attempts < 3
            item.status = "pending" if retry_webhook else "failed"
            if retry_webhook:
                item.available_at = datetime.now(timezone.utc) + timedelta(seconds=30 * 2 ** (item.attempts - 1))
            from app.agents.scout_agent import ProviderError
            item.error_message = str(exc) if isinstance(exc, ProviderError) else f"{type(exc).__name__}: Task failed; verify provider and database connectivity, then retry."
            item.leased_until = None
            if item.kind == "draft":
                lead = await db.get(Lead, uuid.UUID(item.payload["lead_id"]))
                if lead and not lead.opted_out and lead.status not in ("contacted", "replied"):
                    lead.status = "draft_failed"
            elif item.kind == "scrape":
                job = await db.get(Job, uuid.UUID(item.payload["job_id"]))
                if job:
                    job.status = "failed"
                    job.error_message = item.error_message
            await db.commit()
            await publish(str(item.user_id), "task_failed", str(item.id))
            logger.warning("Task %s failed (%s)", item_id, type(exc).__name__)


async def main():
    await create_tables()
    from app.core.database import get_redis
    while not stopping.is_set():
        try:
            redis = await get_redis()
            if redis is not None:
                try:
                    await redis.set("hydps2:worker:heartbeat", "alive", ex=300)
                except Exception:
                    pass
            claims = await claim_work(settings.worker_concurrency)
            if claims:
                await asyncio.gather(*(process_work(item_id) for item_id in claims))
            else:
                try:
                    await asyncio.wait_for(stopping.wait(), settings.worker_poll_seconds)
                except asyncio.TimeoutError:
                    pass
        except Exception as exc:
            logger.warning("Worker waiting for SQL database (%s)", type(exc).__name__)
            await asyncio.sleep(5)
    await close_connections()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: stopping.set())
    asyncio.run(main())
