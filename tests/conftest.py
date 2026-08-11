import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

# conftest.py now lives in tests/, but main.py (the app being tested) lives
# one level up at the project root - that's where uvicorn needs to run from.
ROOT = Path(__file__).parent.parent
# Deliberately NOT port 8000 - that's the port a developer typically runs
# their own local server on while testing client.html by hand. Using a
# different port means the test suite always starts its own isolated
# instance instead of accidentally talking to whatever's already running.
TEST_PORT = 8123
BASE_URL = f"http://localhost:{TEST_PORT}"


def _wait_for_server(timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            requests.get(BASE_URL, timeout=1)
            return True
        except requests.exceptions.ConnectionError:
            time.sleep(0.3)
    return False


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    """Starts the real server as a subprocess against an isolated, throwaway
    database, for the whole test session. Using sys.executable (rather than a
    hardcoded venv path) keeps this portable between local dev and CI."""
    db_path = tmp_path_factory.mktemp("data") / "test_chat.db"

    env = os.environ.copy()
    env["CHAT_DB_PATH"] = str(db_path)
    env["JWT_SECRET_KEY"] = "test-secret-key"
    # tests all connect from 127.0.0.1, so raise the rate limits well above
    # anything a real single client would hit, instead of disabling them
    env["REGISTER_RATE_LIMIT"] = "1000/minute"
    env["LOGIN_RATE_LIMIT"] = "1000/minute"
    # Deliberately fake, not the real key from a local .env (os.environ.copy()
    # would otherwise leak it in here, since auth.py's load_dotenv() runs
    # inside the server subprocess). This makes Gemini "enabled" so the
    # invite/mention flow is testable end-to-end, while guaranteeing every
    # actual API call fails - so tests never hit real Gemini quota or cost.
    env["GEMINI_API_KEY"] = "fake-test-key-for-deterministic-testing"
    # Point at an address nothing listens on, instead of letting the fake
    # key hit Google's real servers - learned the hard way that how fast
    # Google rejects a bad key varies a lot (sometimes ~instant, sometimes
    # slow enough to look like a hang), which made the suite flaky. Failing
    # against a local, non-routable address is instant and 100% deterministic
    # regardless of network conditions or which model GEMINI_MODEL resolves to.
    env["GEMINI_BASE_URL"] = "http://127.0.0.1:1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(TEST_PORT)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if not _wait_for_server():
        proc.terminate()
        out, err = proc.communicate(timeout=5)
        raise RuntimeError(f"Server failed to start.\nstdout:\n{out}\nstderr:\n{err}")

    yield BASE_URL

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
