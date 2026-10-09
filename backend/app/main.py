"""FastAPI Application Entrypoint for HYD-PS2."""

from contextlib import asynccontextmanager
import asyncio
from fastapi import FastAPI, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from pymongo.errors import PyMongoError
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import create_tables, close_connections, engine, get_mongo_db, get_redis
from app.core.security import get_operator_user
from app.agents.scout_agent import ProviderError
from app.api.auth import router as auth_router
from app.api.onboarding import router as onboarding_router
from app.api.leads import router as leads_router
from app.api.voice import router as voice_router
from app.api.decisions import router as decisions_router
from app.api.jobs import router as jobs_router
from app.api.client import router as client_router
from app.api.communications import router as communications_router
from app.api.campaigns import router as campaigns_router
from app.api.scheduling import router as scheduling_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure tables exist in PostgreSQL
    await create_tables()
    yield
    # Shutdown
    await close_connections()


app = FastAPI(
    title=settings.app_name,
    description="AI-Powered Lead Generation & Outreach Platform with Human-in-the-Loop Learning & ElevenLabs Voice Agents",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(auth_router, prefix="/api/v1")
app.include_router(onboarding_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")
app.include_router(leads_router, prefix="/api/v1")
app.include_router(voice_router, prefix="/api/v1")
app.include_router(decisions_router, prefix="/api/v1")
app.include_router(client_router, prefix="/api/v1")
app.include_router(communications_router, prefix="/api/v1")
app.include_router(campaigns_router, prefix="/api/v1")
app.include_router(scheduling_router, prefix="/api/v1")


@app.exception_handler(ProviderError)
async def provider_error(request, exc):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(RuntimeError)
async def integration_error(request, exc):
    return JSONResponse(status_code=502, content={"detail": "An integration is unavailable. Please retry shortly."})


@app.exception_handler(SQLAlchemyError)
async def sql_error(request, exc):
    return JSONResponse(status_code=503, content={"detail": "The application database is unavailable. Please retry shortly."})


@app.exception_handler(PyMongoError)
async def mongo_error(request, exc):
    return JSONResponse(status_code=503, content={"detail": "Conversation memory is unavailable. Please retry shortly."})


@app.get("/health")
async def health_check():
    return {"status": "alive", "app": settings.app_name, "env": settings.app_env}


async def dependency_status():
    async def sql_check():
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    async def mongo_check():
        mongo = await get_mongo_db()
        await mongo.command("ping")
    async def redis_check():
        cache = await get_redis()
        if cache is None:
            raise RuntimeError("Cache not initialized")
        await cache.ping()
    results = await asyncio.gather(*(asyncio.wait_for(check(), 3) for check in (sql_check, mongo_check, redis_check)), return_exceptions=True)
    return dict(zip(("database", "memory", "cache"), ("online" if not isinstance(result, Exception) else "offline" for result in results)))


@app.get("/ready")
async def readiness():
    dependencies = await dependency_status()
    ready = all(dependencies[name] == "online" for name in ("database", "memory"))
    return JSONResponse(status_code=200 if ready else 503, content={"status": "ready" if ready else "degraded", "dependencies": dependencies})


@app.get("/api/v1/system/status")
async def system_status(user=Depends(get_operator_user)):
    dependencies = await dependency_status()
    cache = await get_redis()
    worker = False
    if cache is not None:
        try:
            worker = bool(await cache.get("hydps2:worker:heartbeat"))
        except Exception:
            pass
    return {"dependencies": dependencies, "worker": "online" if worker else "offline",
            "llm_configured": bool(settings.openrouter_api_key or settings.groq_api_key or settings.gemini_api_key),
            "voice_configured": bool(settings.elevenlabs_api_key and settings.elevenlabs_agent_id and settings.elevenlabs_operator_agent_id),
            "email_enabled": settings.email_enabled, "sender": settings.email_from,
            "email_provider": settings.email_provider, "dry_run": settings.email_provider == "smtp",
            "reply_webhook_configured": bool(settings.resend_webhook_secret and settings.receiving_domain),
            "sms_enabled": settings.sms_enabled,
            "sms_webhook_configured": bool(settings.textbee_webhook_secret)}
