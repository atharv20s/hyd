"""Database connection setup for PostgreSQL (async), MongoDB, and Redis."""

import logging
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text, inspect
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
import redis.asyncio as aioredis

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass


# ── PostgreSQL (SQLAlchemy async) ──

engine_options = {"pool_pre_ping": True}
if not settings.database_url.startswith("sqlite"):
    engine_options.update(pool_size=10, connect_args={"timeout": 5})
engine = create_async_engine(settings.database_url, echo=False, **engine_options)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    """FastAPI dependency: yields an async database session."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def create_tables():
    """Ensure PostgreSQL tables exist on startup."""
    try:
        from app.models.models import User, Lead, Job, SendLog, WorkItem
        async with engine.begin() as conn:
            if engine.dialect.name == "postgresql":
                await conn.execute(text("SELECT pg_advisory_xact_lock(8147821)"))
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_upgrade_existing_tables)
        logger.info("PostgreSQL tables checked / created.")
    except Exception as e:
        logger.warning("SQL database unavailable on startup (%s)", type(e).__name__)

    # Initialize Mongo
    await init_mongo()
    await init_redis()


def _upgrade_existing_tables(conn):
    """Additive compatibility migration; preserves all existing lead data."""
    additions = {
        "users": {"account_role": "VARCHAR(20) NOT NULL DEFAULT 'operator'", "client_lead_id": "UUID"},
        "leads": {"job_id": "UUID", "pitch_hook": "TEXT DEFAULT ''", "draft_subject": "VARCHAR(500) DEFAULT ''", "draft_body": "TEXT DEFAULT ''", "raw_data": "JSON"},
        "jobs": {"error_message": "TEXT", "location_source": "VARCHAR(20) NOT NULL DEFAULT 'manual'", "location_captured_at": "TIMESTAMP WITH TIME ZONE", "radius_m": "INTEGER NOT NULL DEFAULT 5000", "prepare_drafts": "BOOLEAN NOT NULL DEFAULT FALSE", "draft_limit": "INTEGER NOT NULL DEFAULT 10", "drafts_queued": "INTEGER NOT NULL DEFAULT 0", "excluded_count": "INTEGER NOT NULL DEFAULT 0"},
        "send_log": {"recipient": "VARCHAR(500) DEFAULT ''", "body": "TEXT DEFAULT ''", "resend_id": "VARCHAR(255)", "error_message": "TEXT"},
        "communications": {"client_user_id": "UUID", "provider_message_id": "VARCHAR(500) DEFAULT ''"},
    }
    inspector = inspect(conn)
    additions["jobs"]["campaign_id"] = "UUID"
    for table, columns in additions.items():
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {definition}'))


# ── MongoDB (Motor async) ──

mongo_client: AsyncIOMotorClient = None
mongo_db = None


async def init_mongo():
    """Initialize MongoDB connection."""
    global mongo_client, mongo_db
    try:
        mongo_client = AsyncIOMotorClient(settings.mongo_url, serverSelectionTimeoutMS=3000)
        mongo_db = mongo_client[settings.mongo_db]
        logger.info("MongoDB client initialized")
    except Exception as e:
        logger.warning("Could not initialize MongoDB client: %s", e)


async def get_mongo_db():
    """FastAPI dependency: returns the MongoDB database handle."""
    global mongo_db
    if mongo_db is None:
        await init_mongo()
    if mongo_db is None:
        raise HTTPException(503, "Conversation memory is unavailable. Please retry shortly.")
    return mongo_db


get_mongo = get_mongo_db


async def close_connections():
    """Close all database connections on app shutdown."""
    global mongo_client, redis_client
    if mongo_client is not None:
        mongo_client.close()
    if redis_client is not None:
        await redis_client.close()
    await engine.dispose()
    logger.info("Database connections closed.")


# ── Redis ──

redis_client: aioredis.Redis = None


async def init_redis():
    """Initialize Redis connection."""
    global redis_client
    try:
        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
    except Exception as e:
        logger.warning("Could not initialize Redis: %s", e)


async def get_redis() -> aioredis.Redis:
    """FastAPI dependency: returns the Redis client."""
    return redis_client
