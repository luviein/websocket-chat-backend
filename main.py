import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.sessions import SessionMiddleware

import auth
import database
from oauth import GOOGLE_OAUTH_ENABLED, oauth_client

VALID_STATUSES = {"available", "away", "invisible"}
DEFAULT_STATUS = "available"

# overridable so tests (which all hit the server from 127.0.0.1) aren't
# throttled by limits meant for real, separate clients
REGISTER_RATE_LIMIT = os.environ.get("REGISTER_RATE_LIMIT", "5/minute")
LOGIN_RATE_LIMIT = os.environ.get("LOGIN_RATE_LIMIT", "10/minute")


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    yield


app = FastAPI(lifespan=lifespan)

# rate limit /register and /login to make brute-force/spam registration harder
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# NOTE: wide open for local dev/practice only - a real app should restrict
# this to the actual frontend's origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# required by authlib to store OAuth state/nonce across the redirect to
# Google and back
app.add_middleware(SessionMiddleware, secret_key=auth.SECRET_KEY)


class ConnectionManager:
    def __init__(self):
        # room_id -> {websocket: {"username": str, "status": str}}
        self.rooms: dict[str, dict[WebSocket, dict]] = {}

    async def connect(self, websocket: WebSocket, room_id: str, username: str):
        await websocket.accept()
        self.rooms.setdefault(room_id, {})[websocket] = {
            "username": username,
            "status": DEFAULT_STATUS,
        }

    def disconnect(self, websocket: WebSocket, room_id: str):
        del self.rooms[room_id][websocket]
        if not self.rooms[room_id]:
            del self.rooms[room_id]

    def set_status(self, websocket: WebSocket, room_id: str, status: str):
        self.rooms[room_id][websocket]["status"] = status

    async def broadcast(self, message: dict, room_id: str):
        for connection in self.rooms.get(room_id, {}):
            await connection.send_json(message)

    def presence_snapshot(self, room_id: str) -> list[dict]:
        return [
            {"username": info["username"], "status": info["status"]}
            for info in self.rooms.get(room_id, {}).values()
        ]


manager = ConnectionManager()


class UserCredentials(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    # max_length=72 matches bcrypt's hard limit (longer passwords error at hash time)
    password: str = Field(min_length=8, max_length=72)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@app.get("/")
def health_check():
    return {"status": "chat backend running"}


@app.post("/register", response_model=TokenResponse)
@limiter.limit(REGISTER_RATE_LIMIT)
def register(request: Request, credentials: UserCredentials):
    hashed = auth.hash_password(credentials.password)
    created = database.create_user(credentials.username, hashed)
    if not created:
        raise HTTPException(status_code=400, detail="Username already taken")
    token = auth.create_access_token(credentials.username)
    return TokenResponse(access_token=token)


@app.post("/login", response_model=TokenResponse)
@limiter.limit(LOGIN_RATE_LIMIT)
def login(request: Request, credentials: UserCredentials):
    user = database.get_user(credentials.username)
    # user["hashed_password"] is None for Google-only accounts - they have no
    # local password to check against, so treat that the same as no match
    if (
        user is None
        or user["hashed_password"] is None
        or not auth.verify_password(credentials.password, user["hashed_password"])
    ):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = auth.create_access_token(credentials.username)
    return TokenResponse(access_token=token)


@app.get("/client.html")
def serve_client():
    # serves the test client over http:// instead of file:// - needed so the
    # Google OAuth redirect below has a real URL to send the user back to
    return FileResponse(Path(__file__).parent / "client.html")


@app.get("/auth/google/login")
async def google_login(request: Request):
    if not GOOGLE_OAUTH_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Google OAuth is not configured (missing GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET)",
        )
    redirect_uri = request.url_for("google_callback")
    return await oauth_client.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/google/callback", name="google_callback")
async def google_callback(request: Request):
    if not GOOGLE_OAUTH_ENABLED:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured on this server")

    token = await oauth_client.google.authorize_access_token(request)
    userinfo = token.get("userinfo")
    if not userinfo or "email" not in userinfo:
        raise HTTPException(status_code=400, detail="Google did not return an email address")

    email = userinfo["email"]
    google_id = userinfo["sub"]

    existing = database.get_user(email)
    if existing is None:
        database.create_google_user(email, google_id)
    elif existing["google_id"] != google_id:
        # a password-based account already owns this email - don't silently
        # merge identities
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists. Log in with your password instead.",
        )

    jwt_token = auth.create_access_token(email)
    return RedirectResponse(url=f"/client.html?token={jwt_token}")


async def broadcast_presence(room_id: str):
    await manager.broadcast(
        {"type": "presence", "users": manager.presence_snapshot(room_id)}, room_id
    )


@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, token: str = Query(...)):
    username = auth.decode_access_token(token)
    if username is None:
        await websocket.close(code=1008)  # policy violation: bad/missing token
        return

    await manager.connect(websocket, room_id, username)

    # send this room's recent history to the newly connected client only
    history = database.get_recent_messages(room_id)
    if history:
        await websocket.send_json({"type": "system", "content": "Messages you have missed:"})
        for row in history:
            await websocket.send_json(
                {"type": "chat", "username": row["username"], "content": row["content"]}
            )

    await manager.broadcast({"type": "system", "content": f"{username} joined the room"}, room_id)
    await broadcast_presence(room_id)

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "chat":
                content = data.get("content", "")
                database.save_message(room_id, username, content)
                await manager.broadcast(
                    {"type": "chat", "username": username, "content": content}, room_id
                )

            elif msg_type == "set_status":
                status = data.get("status")
                if status in VALID_STATUSES:
                    manager.set_status(websocket, room_id, status)
                    await broadcast_presence(room_id)

    except WebSocketDisconnect:
        manager.disconnect(websocket, room_id)
        await manager.broadcast({"type": "system", "content": f"{username} left the room"}, room_id)
        if room_id in manager.rooms:
            await broadcast_presence(room_id)
