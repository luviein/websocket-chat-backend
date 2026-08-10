# Chat Backend

A WebSocket-based chat backend built with FastAPI, for practicing real-time backend patterns.

## Setup

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```
uvicorn main:app --reload
```

Server runs at `http://localhost:8000`.

## Try it

Open `client.html` directly in your browser (or serve it). Enter a room name and username, connect from two tabs using the **same room**, and chat between them. Tabs using a *different* room won't see those messages.

## Tests

`test_rooms.py` is a small integration test that connects multiple WebSocket clients and verifies messages stay scoped to their room:

```
python test_rooms.py
```

(Run this while the server is up in another terminal.)

## Roadmap

- [x] MVP: single global chat room, broadcast to all connected clients
- [x] Rooms/channels: join a specific room, messages scoped to that room
- [ ] Persistence: store message history in a database (SQLite/Postgres)
- [ ] Auth: real user identity instead of a free-text username
- [ ] Stretch: typing indicators, presence status, private DMs
