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

## Auth

Clients must register/login to get a JWT token before connecting:

```
POST /register  { "username": "...", "password": "..." }
POST /login     { "username": "...", "password": "..." }
```

Both return `{ "access_token": "...", "token_type": "bearer" }`. Connect to the
WebSocket with that token instead of a free-text username:

```
ws://localhost:8000/ws/{room_id}?token={access_token}
```

The server decodes the username from the token server-side — clients can no
longer just claim to be anyone by typing a name in the URL.

## Tests

Run these while the server is up in another terminal:

```
python test_rooms.py         # messages stay scoped to their room
python test_persistence.py   # message history persists and replays to new joiners
python test_auth.py          # register/login/token flow, and WebSocket auth enforcement
python test_presence.py      # new joiners see who's already online in the room
```

## Roadmap

- [x] MVP: single global chat room, broadcast to all connected clients
- [x] Rooms/channels: join a specific room, messages scoped to that room
- [x] Persistence: store message history in a database (SQLite), replayed to new joiners
- [x] Auth: JWT-based login, WebSocket identifies users from their token
- [x] Presence status: new joiners see who's currently online in the room
- [ ] Stretch: typing indicators, private DMs
