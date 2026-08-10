from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import auth
import database


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    yield


app = FastAPI(lifespan=lifespan)

# NOTE: wide open for local dev/practice only - a real app should restrict
# this to the actual frontend's origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConnectionManager:
    def __init__(self):
        self.rooms: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, room_id: str):
        await websocket.accept()
        self.rooms.setdefault(room_id, []).append(websocket)

    def disconnect(self, websocket: WebSocket, room_id: str):
        self.rooms[room_id].remove(websocket)
        if not self.rooms[room_id]:
            del self.rooms[room_id]

    async def broadcast(self, message: str, room_id: str):
        for connection in self.rooms.get(room_id, []):
            await connection.send_text(message)


manager = ConnectionManager()


class UserCredentials(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@app.get("/")
def health_check():
    return {"status": "chat backend running"}


@app.post("/register", response_model=TokenResponse)
def register(credentials: UserCredentials):
    hashed = auth.hash_password(credentials.password)
    created = database.create_user(credentials.username, hashed)
    if not created:
        raise HTTPException(status_code=400, detail="Username already taken")
    token = auth.create_access_token(credentials.username)
    return TokenResponse(access_token=token)


@app.post("/login", response_model=TokenResponse)
def login(credentials: UserCredentials):
    user = database.get_user(credentials.username)
    if user is None or not auth.verify_password(credentials.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = auth.create_access_token(credentials.username)
    return TokenResponse(access_token=token)


@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, token: str = Query(...)):
    username = auth.decode_access_token(token)
    if username is None:
        await websocket.close(code=1008)  # policy violation: bad/missing token
        return

    await manager.connect(websocket, room_id)

    # send this room's recent history to the newly connected client only
    history = database.get_recent_messages(room_id)
    if history:
        await websocket.send_text("--- Messages you have missed ---")
        for row in history:
            await websocket.send_text(f"{row['username']}: {row['content']}")

    await manager.broadcast(f"{username} joined the room", room_id)
    try:
        while True:
            data = await websocket.receive_text()
            database.save_message(room_id, username, data)
            await manager.broadcast(f"{username}: {data}", room_id)
    except WebSocketDisconnect:
        manager.disconnect(websocket, room_id)
        await manager.broadcast(f"{username} left the room", room_id)
