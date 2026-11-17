"""
Centralised configuration, loaded once from environment variables (backend/.env).
"""

import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # GitHub OAuth app (only needed for "Auto-review my repos")
    GITHUB_CLIENT_ID: str = os.getenv("GITHUB_CLIENT_ID", "")
    GITHUB_CLIENT_SECRET: str = os.getenv("GITHUB_CLIENT_SECRET", "")
    # Written onto every webhook we create; used to verify X-Hub-Signature-256.
    GITHUB_WEBHOOK_SECRET: str = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    # Optional personal token for "Review a PR link" when nobody is signed in.
    # Without it, GitHub allows 60 unauthenticated requests per hour.
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")

    # Google Gemini (free tier): https://aistudio.google.com/app/apikey
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    # Comma-separated, tried in order. A 429/503 on one model falls through to the next.
    GEMINI_MODELS: list[str] = [
        m.strip()
        for m in os.getenv("GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.5-flash").split(",")
        if m.strip()
    ]
    # Per-request timeout. Busy free-tier models can hang for a minute or more;
    # timing out moves on to the next model instead.
    GEMINI_TIMEOUT_S: int = int(os.getenv("GEMINI_TIMEOUT_S", "60"))
    AGENT_MAX_TURNS: int = int(os.getenv("AGENT_MAX_TURNS", "8"))

    # Postgres (Neon) in production; SQLite file by default so the project runs with no setup.
    DATABASE_URL: str = os.getenv("DATABASE_URL", "") or "sqlite+aiosqlite:///./reviews.db"

    JWT_SECRET: str = os.getenv("JWT_SECRET", "change-me-in-production")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_HOURS: int = 24

    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:5173")
    BACKEND_URL: str = os.getenv("BACKEND_URL", "http://localhost:8000")
    # Public URL GitHub can reach (ngrok in local dev). Falls back to BACKEND_URL.
    WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "") or os.getenv("BACKEND_URL", "http://localhost:8000")

    @property
    def CORS_ORIGINS(self) -> list[str]:
        extra = os.getenv("CORS_ORIGINS", "")
        origins = [self.FRONTEND_URL]
        origins.extend(o.strip() for o in extra.split(",") if o.strip())
        return origins


settings = Settings()
