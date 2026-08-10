from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

import database


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    yield


app = FastAPI(lifespan=lifespan)


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


@app.get("/")
def health_check():
    return {"status": "chat backend running"}


@app.websocket("/ws/{room_id}/{username}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, username: str):
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
