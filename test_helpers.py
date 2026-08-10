import uuid

import requests

BASE = "http://localhost:8000"


def get_token(name: str) -> tuple[str, str]:
    """Registers a fresh, uniquely-named user and returns (username, token)."""
    username = f"{name}-{uuid.uuid4().hex[:6]}"
    res = requests.post(f"{BASE}/register", json={"username": username, "password": "test-password"})
    res.raise_for_status()
    return username, res.json()["access_token"]
