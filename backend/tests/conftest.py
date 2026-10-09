import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from mongomock_motor import AsyncMongoMockClient
from app.main import app
from app.core.database import Base, get_db, get_mongo_db
from app.core import database
from app import worker


@pytest_asyncio.fixture
async def harness(tmp_path, monkeypatch):
    # Legacy contract tests explicitly exercise the live adapter with mock HTTP.
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "email_provider", "resend")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    mongo = AsyncMongoMockClient()["test"]
    async def db_dependency():
        async with factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise
    async def mongo_dependency():
        return mongo
    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_mongo_db] = mongo_dependency
    monkeypatch.setattr(worker, "async_session", factory)
    monkeypatch.setattr(worker, "get_mongo_db", mongo_dependency)
    monkeypatch.setattr(database, "redis_client", None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, factory, mongo
    app.dependency_overrides.clear()
    await engine.dispose()


async def signup(client, email="operator@example.com", invite=None):
    response = await client.post("/api/v1/auth/signup", json={"email": email, "password": "correct-password", "full_name": "Test Operator", "invite_token": invite})
    assert response.status_code == 200, response.text
    return response.json(), {"Authorization": f"Bearer {response.json()['access_token']}"}
