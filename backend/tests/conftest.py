import os
import sys
import tempfile
from pathlib import Path

# Isolated config before any app module is imported.
_db = Path(tempfile.mkdtemp()) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db}"
os.environ["GITHUB_WEBHOOK_SECRET"] = "test-secret"
os.environ["GEMINI_API_KEY"] = "test-key"
os.environ["JWT_SECRET"] = "test-jwt-secret-that-is-long-enough-1234"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
