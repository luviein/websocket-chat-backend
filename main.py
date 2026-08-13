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


def dm_room_id(user_a: str, user_b: str) -> str:
    """Deterministic room id for a DM between two display names, the same
    regardless of who initiates - both sides compute the identical id, so a
    DM is just a regular room under the hood with no separate storage/
    broadcast machinery needed. Relies on display names never containing
    ':' (enforced at registration/signup below) so the two names can't be
    ambiguously re-split apart, and requires the "dm:" prefix be treated as
    reserved for regular free-text room names."""
    return "dm:" + ":".join(sorted([user_a, user_b]))


def dm_participants(room_id: str) -> tuple[str, str] | None:
    """Returns the two display names a DM room id encodes, or None if
    room_id isn't a (well-formed) DM room at all."""
    if not room_id.startswith("dm:"):
        return None
    parts = room_id[len("dm:"):].split(":")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]

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

    async def broadcast(
        self,
        message: dict,
        room_id: str,
        exclude: WebSocket | None = None,
        skip_usernames: set[str] | None = None,
    ):
        for connection, info in self.rooms.get(room_id, {}).items():
            if connection is exclude:
                continue
            if skip_usernames and info["username"] in skip_usernames:
                continue
            await connection.send_json(message)

    def has_connection(self, room_id: str, username: str) -> bool:
        return any(info["username"] == username for info in self.rooms.get(room_id, {}).values())

    def connections_for_username(self, username: str) -> list[WebSocket]:
        """Every connection this user currently has open, across all rooms -
        used to notify them of a new DM on whatever else they're connected
        to, since they're by definition not in the DM room itself right now."""
        return [
            conn
            for room_conns in self.rooms.values()
            for conn, info in room_conns.items()
            if info["username"] == username
        ]

    def invite_gemini(self, room_id: str):
        self.gemini_rooms.add(room_id)

    def is_gemini_active(self, room_id: str) -> bool:
        return room_id in self.gemini_rooms

    async def broadcast_global(self, message: dict):
        """Sends to every connection this server has, in any room - used for
        the "who's online anywhere" roster, since that's not scoped to a
        single room the way normal presence is."""
        for room_conns in self.rooms.values():
            for conn in room_conns:
                await conn.send_json(message)

    def global_roster(self) -> list[dict]:
        """One entry per unique display name across every room/DM someone is
        currently connected to (a user with two tabs open in different rooms
        only appears once). Gemini is deliberately excluded - it's per-room
        bot presence, not a real account you'd look up here to DM."""
        seen: dict[str, str] = {}
        for room_conns in self.rooms.values():
            for info in room_conns.values():
                seen[info["username"]] = info["status"]
        return [{"username": username, "status": status} for username, status in seen.items()]

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


@app.get("/health")
def health_check():
    return {"status": "chat backend running"}


@app.get("/")
def serve_root():
    return FileResponse(Path(__file__).parent / "static" / "client.html")


@app.post("/register", response_model=TokenResponse)
@limiter.limit(REGISTER_RATE_LIMIT)
def register(request: Request, credentials: UserCredentials):
    if ":" in credentials.username:
        # ':' is reserved as the separator inside DM room ids (dm_room_id
        # above) - allowing it in a username would let two different names
        # collide into the same encoded DM room, or split apart wrong
        raise HTTPException(status_code=400, detail="Username cannot contain ':'")
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

    if ":" in choice.display_name:  # see the matching check in register() above
        raise HTTPException(status_code=400, detail="Display name cannot contain ':'")

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


async def broadcast_global_presence():
    await manager.broadcast_global({"type": "global_presence", "users": manager.global_roster()})


async def force_close_dm(room_id: str, target_display_name: str):
    """Disconnects (only) target_display_name's active connection(s) to a DM
    room - used right after a block, so an already-open conversation doesn't
    keep working until the blocked side happens to reconnect on their own.
    Only the blocked person's side is closed, not the blocker's - they stay
    in the room free to leave on their own terms."""
    connections = [
        conn for conn, info in manager.rooms.get(room_id, {}).items() if info["username"] == target_display_name
    ]
    for conn in connections:
        await conn.send_json({"type": "system", "content": "You can't message this user."})
        await conn.close(code=1008)


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

    # a "dm:" room id encodes exactly the two people allowed in it - reject
    # anyone else (or a malformed dm: id) before ever accepting the socket,
    # same as the bad-token case above
    participants = dm_participants(room_id)
    if participants is not None:
        if display_name not in participants:
            await websocket.close(code=1008)
            return
        other = participants[1] if participants[0] == display_name else participants[0]
        if database.is_blocked_pair(display_name, other):
            # closed pre-accept (no message) same as the "not a participant"
            # case above - lets the client tell "rejected" apart from "the
            # DM briefly opened" just from whether onopen ever fired, so it
            # can show a quick popup instead of navigating into an empty
            # DM view first
            await websocket.close(code=1008)
            return

    await manager.connect(websocket, room_id, display_name)

    # lets the client know its own display name (needed to build DM room ids
    # and to exclude itself from Message/Block buttons in the presence
    # grid), and its current block list (to show Block vs. Unblock). No
    # explicit early global_presence send here (unlike self/block_list) -
    # broadcast_global_presence() below already reaches this connection too,
    # same as how room-scoped "presence" only ever arrives via its broadcast.
    await websocket.send_json({"type": "self", "username": display_name})
    await websocket.send_json({"type": "block_list", "blocked": database.get_blocked_users(display_name)})

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
    await broadcast_global_presence()

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
                # withhold this message from anyone who has blocked the
                # sender - silently, same as Discord: the sender isn't
                # restricted or notified in any way, they just don't know
                # that specific person no longer sees their messages. Never
                # affects the sender's own copy (you can't block yourself).
                blockers = set(database.get_blockers_of(display_name))
                await manager.broadcast(
                    {"type": "chat", "username": display_name, "content": content},
                    room_id,
                    skip_usernames=blockers,
                )

                # if this is a DM and the other person isn't actively looking
                # at it right now, they'd otherwise never know a message
                # arrived - nudge them on whatever else they're connected to
                dm_participants_here = dm_participants(room_id)
                if dm_participants_here is not None:
                    other = (
                        dm_participants_here[1]
                        if dm_participants_here[0] == display_name
                        else dm_participants_here[0]
                    )
                    if not manager.has_connection(room_id, other):
                        for conn in manager.connections_for_username(other):
                            await conn.send_json({"type": "dm_notification", "from": display_name})

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
                    await broadcast_global_presence()

            elif msg_type == "block":
                target = data.get("username", "")
                if target and target != display_name:
                    database.block_user(display_name, target)
                    await force_close_dm(dm_room_id(display_name, target), target)
                    await websocket.send_json(
                        {"type": "block_list", "blocked": database.get_blocked_users(display_name)}
                    )

            elif msg_type == "unblock":
                target = data.get("username", "")
                if target:
                    database.unblock_user(display_name, target)
                    await websocket.send_json(
                        {"type": "block_list", "blocked": database.get_blocked_users(display_name)}
                    )

    except WebSocketDisconnect:
        manager.disconnect(websocket, room_id)
        await manager.broadcast({"type": "system", "content": f"{display_name} left the room"}, room_id)
        if room_id in manager.rooms:
            await broadcast_presence(room_id)
        await broadcast_global_presence()
