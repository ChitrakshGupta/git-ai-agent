"""
PR Review Agent: FastAPI backend.
"""

import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import Base, engine
from app.routers.auth import router as auth_router
from app.routers.repos import router as repos_router
from app.routers.reviews import router as reviews_router
from app.routers.webhook import router as webhook_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# Public base URL GitHub delivers webhooks to. Replaced by the ngrok URL in local dev.
public_webhook_url: str = settings.WEBHOOK_URL


def _start_ngrok_background(port: int) -> None:
    """Open an ngrok tunnel in a daemon thread so startup never blocks on it."""

    def _tunnel():
        global public_webhook_url
        try:
            import shutil

            from pyngrok import conf, ngrok

            # Prefer an installed ngrok (already logged in via its own config) over pyngrok's download.
            if shutil.which("ngrok"):
                conf.get_default().ngrok_path = shutil.which("ngrok")
            if os.getenv("NGROK_AUTHTOKEN"):
                conf.get_default().auth_token = os.getenv("NGROK_AUTHTOKEN")
            tunnels = ngrok.get_tunnels()
            url = tunnels[0].public_url if tunnels else ngrok.connect(str(port), "http").public_url
            public_webhook_url = url.replace("http://", "https://")  # GitHub requires https
            print(f"🌐 ngrok tunnel: {public_webhook_url}/webhook/github")
        except Exception as exc:
            print(f"⚠️  ngrok not started ({exc}); webhooks need a public URL")

    threading.Thread(target=_tunnel, daemon=True).start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    import app.models  # noqa: F401  registers tables on Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print(f"✅ Database ready ({engine.url.get_backend_name()})")
    if not settings.GEMINI_API_KEY:
        print("⚠️  GEMINI_API_KEY is not set: reviews will fail until it is")
    if os.getenv("ENABLE_NGROK", "").lower() in ("1", "true", "yes") and settings.WEBHOOK_URL.startswith("http://localhost"):
        _start_ngrok_background(8000)
    yield
    await engine.dispose()


app = FastAPI(title="PR Review Agent", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(repos_router)
app.include_router(reviews_router)
app.include_router(webhook_router)


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {
        "status": "ok",
        "database": engine.url.get_backend_name(),
        "ai_configured": bool(settings.GEMINI_API_KEY),
        "oauth_configured": bool(settings.GITHUB_CLIENT_ID),
        "webhook_url": f"{public_webhook_url}/webhook/github",
    }
