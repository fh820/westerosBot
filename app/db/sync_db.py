import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Determine the environment
env_state = os.getenv("APP_ENV", "LOCAL").upper()  # Default to LOCAL if missing

if env_state == "DEV":
    DATABASE_URL = os.getenv("DB_URL_DEV")
    print("🌍 MODE: DEV (Connected to Supabase Cloud)")
else:
    DATABASE_URL = os.getenv("DB_URL_LOCAL")
    print("🌍 MODE: LOCAL")

if not DATABASE_URL:
    raise ValueError("Database URL is not set correctly in .env file")

# For async usage
DB_URL_ASYNC = DATABASE_URL

# For sync usage (SQLAlchemy standard)
DB_URL_SYNC = DB_URL_ASYNC.replace("postgresql+asyncpg", "postgresql+psycopg2")

# Create synchronous engine
engine = create_engine(DB_URL_SYNC, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_sync_session():
    """Provides a synchronous SQLAlchemy session for Celery workers or scripts."""
    return SessionLocal()
