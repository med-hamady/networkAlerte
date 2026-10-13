"""
Shared fixtures for integration tests.

The integration DB must be selected explicitly with TEST_DATABASE_URL and have
a test-only database name. Application/production settings are never a fallback.
"""

import os

import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.models import *  # noqa: F401, F403 — registers all models with Base metadata


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest_asyncio.fixture
async def db():
    """
    Explicit isolated PostgreSQL. A connection-level outer transaction survives
    session.commit() calls; session transactions only release their savepoints.
    """
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("TEST_DATABASE_URL must explicitly select an isolated PostgreSQL test DB")
    try:
        url = make_url(raw_url)
    except Exception:
        pytest.fail("Invalid TEST_DATABASE_URL (value withheld)")
    if url.drivername != "postgresql+asyncpg" or not (url.database or "").endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must use postgresql+asyncpg and a database ending in _test")
    engine = create_async_engine(url, echo=False)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                async with AsyncSession(
                    bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint",
                ) as session:
                    yield session
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
