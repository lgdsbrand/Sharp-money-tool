"""Runs before any test module is imported, so the app reads these settings (and a throwaway DB)."""
import os
import pathlib
import tempfile

os.environ["DEMO_MODE"] = "true"
os.environ["REFRESH_SECONDS"] = "0"
os.environ["ADMIN_TOKEN"] = ""
os.environ["REQUIRE_LOGIN"] = "false"
os.environ["SQLITE_PATH"] = os.path.join(tempfile.gettempdir(), "sharp_test.db")
os.environ["SUPABASE_URL"] = ""
pathlib.Path(os.environ["SQLITE_PATH"]).unlink(missing_ok=True)
