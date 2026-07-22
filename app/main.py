import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database.database import Base, engine
from app.api.endpoints import router as api_router
from app.crawler.crawler import crawler_manager
from app.queue.redis_queue import redis_queue
from app.search.embeddings import EmbeddingEngine

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    logger.info("Initializing database tables...")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing database tables: {e}")
        logger.warning("Continuing startup. Database connection may be resolved later.")

    # Connect to Redis
    try:
        await redis_queue.connect()
        await redis_queue.flush()  # Clear any stale queues/visited sets on fresh boot
        logger.info("Redis queue and visited set flushed on startup.")
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        logger.warning("Crawler will not work until Redis is reachable.")

    # Warm up the semantic embedding model in a background thread so it
    # doesn't block the event loop or delay the first API response.
    # _load_model() is CPU-bound; asyncio.to_thread runs it in a thread pool.
    asyncio.create_task(asyncio.to_thread(EmbeddingEngine.is_available))
    logger.info("EmbeddingEngine: warm-up started in background thread.")

    yield

    # --- Shutdown ---
    logger.info("Application shutting down. Halting active crawler tasks...")
    await crawler_manager.stop()
    await redis_queue.disconnect()
    logger.info("Crawler and Redis connection stopped successfully.")

app = FastAPI(
    title="PyCrawler API",
    description="Distributed Web Crawler & Search Engine API",
    version="1.0",
    lifespan=lifespan
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routes
app.include_router(api_router, prefix="/api")

# Serve the Single Page Dashboard
DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), "dashboard")

@app.get("/")
async def get_dashboard():
    """Serves the main dashboard user interface."""
    index_path = os.path.join(DASHBOARD_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "PyCrawler API running. Dashboard UI files not found."}

# Mount static files if additional assets exist
if os.path.exists(DASHBOARD_DIR):
    app.mount("/static", StaticFiles(directory=DASHBOARD_DIR), name="static")
