import os
from contextlib import asynccontextmanager
from typing import AsyncIterator  # <--- IMPORT THIS
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.db.models import Base
from dotenv import load_dotenv

load_dotenv()
env_state = os.getenv("APP_ENV", "DEV").upper()

if env_state == "DEV":
    DATABASE_URL = os.getenv("DB_URL_DEV")
    print("🌍 MODE: DEV (Connected to Supabase Cloud)")
else:
    DATABASE_URL = os.getenv("DB_URL_LOCAL")
    print("💻 MODE: LOCAL (Connected to Laptop/Localhost)")

if not DATABASE_URL:
    raise ValueError(
        f"❌ CRITICAL ERROR: Database URL not found for {env_state} mode. Check your .env file."
    )

# Create the Async Engine
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"statement_cache_size": 0},
    pool_pre_ping=True,  # <-- FIX #1: Checks if the connection is alive before using it.
    pool_recycle=1800,  # <-- FIX #2: Closes and replaces connections older than 30 minutes (1800s).
)

# Async Session Factory
get_session_factory = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db():
    """Create tables if they don't exist"""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    """
                    ALTER TABLE battles
                    ADD COLUMN IF NOT EXISTS phase VARCHAR DEFAULT 'ROUND',
                    ADD COLUMN IF NOT EXISTS round_number INTEGER DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS terrain VARCHAR DEFAULT 'unknown',
                    ADD COLUMN IF NOT EXISTS attacker_morale INTEGER DEFAULT 100,
                    ADD COLUMN IF NOT EXISTS defender_morale INTEGER DEFAULT 100,
                    ADD COLUMN IF NOT EXISTS attacker_plan VARCHAR,
                    ADD COLUMN IF NOT EXISTS defender_plan VARCHAR,
                    ADD COLUMN IF NOT EXISTS attacker_supply INTEGER DEFAULT 100,
                    ADD COLUMN IF NOT EXISTS defender_supply INTEGER DEFAULT 100,
                    ADD COLUMN IF NOT EXISTS wall_integrity INTEGER,
                    ADD COLUMN IF NOT EXISTS blockade_fleet_id INTEGER
                    """
                )
            )
        print("✅ Database Connected & Tables Initialized.")
    except Exception as e:
        print(f"❌ Database Connection Failed: {e}")


# =================================================================
# THIS IS THE CORRECTED FUNCTION SIGNATURE
# =================================================================
@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:  # <--- THE FIX IS HERE
    """
    Provides a SQLAlchemy AsyncSession within a managed transaction.
    This is the correct pattern for handling sessions in an async application.
    """
    async with get_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
