"""
Shared fixtures for integration tests.

db fixture: real AsyncSession on the running postgres, rolled back after each test.
Settings fixture: cached app settings (points to the real DB).
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.models import *  # noqa: F401, F403 — registers all models with Base metadata


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest_asyncio.fixture
async def db(settings):
    """
    Real AsyncSession backed by the running postgres.
    Every test runs inside a transaction that is rolled back on teardown —
    no test data leaks between tests or persists after the suite.
    """
    engine = create_async_engine(settings.database_url, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.begin()
        yield session
        await session.rollback()

    await engine.dispose()
