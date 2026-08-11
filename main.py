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
import gemini
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
        # room_ids that have had "/invite-gemini" used in them
        self.gemini_rooms: set[str] = set()

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
            # room is now empty - don't let Gemini's membership linger for
            # whoever happens to start a new conversation in this room_id later
            self.gemini_rooms.discard(room_id)

    def set_status(self, websocket: WebSocket, room_id: str, status: str):
        self.rooms[room_id][websocket]["status"] = status

    async def broadcast(self, message: dict, room_id: str, exclude: WebSocket | None = None):
        for connection in self.rooms.get(room_id, {}):
            if connection is exclude:
                continue
            await connection.send_json(message)

    def invite_gemini(self, room_id: str):
        self.gemini_rooms.add(room_id)

    def is_gemini_active(self, room_id: str) -> bool:
        return room_id in self.gemini_rooms

    def presence_snapshot(self, room_id: str) -> list[dict]:
        users = [
            {"username": info["username"], "status": info["status"]}
            for info in self.rooms.get(room_id, {}).values()
        ]
        if room_id in self.gemini_rooms:
            users.append({"username": gemini.BOT_NAME, "status": "available"})
        return users


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
    return FileResponse(Path(__file__).parent / "static" / "client.html")


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
        # new account - don't finish creating it yet. Stash the pending
        # signup in the signed session cookie and ask for a display name
        # first, so chat doesn't just show the user's raw email address.
        request.session["pending_google_signup"] = {"email": email, "google_id": google_id}
        return RedirectResponse(url="/client.html?choose_display_name=1")
    elif existing["google_id"] != google_id:
        # a password-based account already owns this email - don't silently
        # merge identities
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists. Log in with your password instead.",
        )

    jwt_token = auth.create_access_token(email)
    return RedirectResponse(url=f"/client.html?token={jwt_token}")


class DisplayNameChoice(BaseModel):
    display_name: str = Field(min_length=1, max_length=30)


@app.post("/auth/google/complete-signup", response_model=TokenResponse)
@limiter.limit(REGISTER_RATE_LIMIT)
def complete_google_signup(request: Request, choice: DisplayNameChoice):
    pending = request.session.get("pending_google_signup")
    if not pending:
        raise HTTPException(
            status_code=400,
            detail="No pending Google sign-in found - please sign in with Google again.",
        )

    created = database.create_google_user(pending["email"], pending["google_id"], choice.display_name)
    if not created:
        raise HTTPException(status_code=400, detail="An account with this email already exists")

    del request.session["pending_google_signup"]
    jwt_token = auth.create_access_token(pending["email"])
    return TokenResponse(access_token=jwt_token)


async def broadcast_presence(room_id: str):
    await manager.broadcast(
        {"type": "presence", "users": manager.presence_snapshot(room_id)}, room_id
    )


@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, token: str = Query(...)):
    account_id = auth.decode_access_token(token)
    if account_id is None:
        await websocket.close(code=1008)  # policy violation: bad/missing token
        return

    # account_id (email for Google accounts, chosen username for password
    # accounts) is the stable, unique identity used for auth/DB lookups.
    # display_name is the cosmetic name resolved from it, used everywhere
    # chat-facing instead - this keeps a user's raw Google email out of
    # every message/presence entry/join notice.
    display_name = database.get_display_name(account_id)

    await manager.connect(websocket, room_id, display_name)

    # send this room's recent history to the newly connected client only
    history = database.get_recent_messages(room_id)
    if history:
        await websocket.send_json({"type": "system", "content": "Messages you have missed:"})
        for row in history:
            await websocket.send_json(
                {"type": "chat", "username": row["username"], "content": row["content"]}
            )

    await manager.broadcast({"type": "system", "content": f"{display_name} joined the room"}, room_id)
    await broadcast_presence(room_id)

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "chat":
                content = data.get("content", "")

                if content.strip().lower() == gemini.INVITE_COMMAND:
                    if not gemini.GEMINI_ENABLED:
                        await websocket.send_json({
                            "type": "system",
                            "content": "Gemini isn't configured on this server (missing GEMINI_API_KEY).",
                        })
                    elif manager.is_gemini_active(room_id):
                        await websocket.send_json(
                            {"type": "system", "content": "Gemini is already in this room."}
                        )
                    else:
                        manager.invite_gemini(room_id)
                        await manager.broadcast(
                            {"type": "system", "content": f"{gemini.BOT_NAME} has joined the room"}, room_id
                        )
                        await broadcast_presence(room_id)
                    continue  # don't save/broadcast the command as a normal chat message

                database.save_message(room_id, display_name, content)
                await manager.broadcast(
                    {"type": "chat", "username": display_name, "content": content}, room_id
                )

                if manager.is_gemini_active(room_id) and content.strip().lower().startswith(
                    gemini.MENTION_PREFIX
                ):
                    prompt = content.strip()[len(gemini.MENTION_PREFIX):].strip()
                    # lets clients show a "Gemini is typing..." indicator
                    # while the (possibly 30s-long) API call is in flight,
                    # instead of a silent gap that looks like nothing is
                    # happening
                    await manager.broadcast({"type": "gemini_typing"}, room_id)
                    reply = await gemini.ask_gemini(prompt)
                    database.save_message(room_id, gemini.BOT_NAME, reply)
                    await manager.broadcast(
                        {"type": "chat", "username": gemini.BOT_NAME, "content": reply}, room_id
                    )

            elif msg_type == "typing":
                # no server-side "stopped typing" tracking/broadcast - kept
                # deliberately simple, clients auto-expire the indicator if
                # no further "typing" signal arrives for a few seconds.
                # excludes the sender - you already know you're typing.
                await manager.broadcast(
                    {"type": "typing", "username": display_name}, room_id, exclude=websocket
                )

            elif msg_type == "set_status":
                status = data.get("status")
                if status in VALID_STATUSES:
                    manager.set_status(websocket, room_id, status)
                    await broadcast_presence(room_id)

    except WebSocketDisconnect:
        manager.disconnect(websocket, room_id)
        await manager.broadcast({"type": "system", "content": f"{display_name} left the room"}, room_id)
        if room_id in manager.rooms:
            await broadcast_presence(room_id)
