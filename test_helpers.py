import uuid

import requests


def get_token(server: str, name: str) -> tuple[str, str]:
    """Registers a fresh, uniquely-named user and returns (username, token)."""
    username = f"{name}-{uuid.uuid4().hex[:6]}"
    res = requests.post(f"{server}/register", json={"username": username, "password": "test-password"})
    res.raise_for_status()
    return username, res.json()["access_token"]


def ws_url(server: str, room: str, token: str) -> str:
    return f"{server.replace('http', 'ws')}/ws/{room}?token={token}"
