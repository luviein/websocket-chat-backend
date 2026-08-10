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

Open `client.html` directly in your browser (or serve it), open it in two tabs with different usernames, and chat between them.

## Roadmap

- [x] MVP: single global chat room, broadcast to all connected clients
- [ ] Rooms/channels: join a specific room, messages scoped to that room
- [ ] Persistence: store message history in a database (SQLite/Postgres)
- [ ] Auth: real user identity instead of a free-text username
- [ ] Stretch: typing indicators, presence status, private DMs
