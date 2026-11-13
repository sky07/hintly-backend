"""FastAPI application entry point."""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from config import settings
from limiter import limiter

# Initialize Sentry if DSN is configured
if settings.sentry_dsn:
    import sentry_sdk
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
    )
from routers import websocket, auth, sessions, user_settings, billing, interview_prep, questionnaire, credits, admin, support
from db.database import init_db, close_db
from services.mongodb_service import init_mongodb, close_mongodb

# Configure logging to show all INFO+ messages
# Wrap stdout to handle Unicode (emojis) on Windows cp1252 consoles
_log_stream = open(sys.stdout.fileno(), mode="w", encoding="utf-8", errors="replace", closefd=False)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    stream=_log_stream,
    force=True,
)

# Set specific loggers to INFO level
logging.getLogger("services.gemini_client").setLevel(logging.INFO)
logging.getLogger("routers.websocket").setLevel(logging.INFO)

logger = logging.getLogger(__name__)
logger.info("===== Interview Assistant Backend Starting =====")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan - startup and shutdown."""
    # Startup
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized")
    logger.info("Initializing MongoDB...")
    await init_mongodb()
    logger.info("MongoDB initialization complete")
    yield
    # Shutdown
    logger.info("Closing database connections...")
    await close_db()
    await close_mongodb()
    logger.info("Database connections closed")


app = FastAPI(
    title="Hintly API",
    description="Real-time interview coaching backend",
    version="1.0.0",
    lifespan=lifespan,
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Configure CORS - use settings.allowed_origins (env var) + hardcoded dev origins
CORS_ORIGINS = settings.origins_list + [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://localhost:3002",
    "http://localhost:3003",
    "http://localhost:3004",
    "http://localhost:3005",
    "http://localhost:9000",  # Desktop app (Electron dev server)
    "https://interview-assistant-frontend-phi.vercel.app",
    "wss://interview-assistant-backend-26gk.vercel.app/ws",
    "https://hintly.tech",
    "https://www.hintly.tech",
]
# Deduplicate
CORS_ORIGINS = list(set(CORS_ORIGINS))
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(user_settings.router)
app.include_router(billing.router)
app.include_router(interview_prep.router)
app.include_router(questionnaire.router)
app.include_router(credits.router)
app.include_router(admin.router)
app.include_router(support.router)
app.include_router(websocket.router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "version": "1.0.0"}


# This is important for Vercel
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
