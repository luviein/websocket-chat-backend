# Chat Backend

[![Tests](https://github.com/luviein/websocket-chat-backend/actions/workflows/tests.yml/badge.svg)](https://github.com/luviein/websocket-chat-backend/actions/workflows/tests.yml)

A WebSocket-based chat backend built with FastAPI, for practicing real-time backend patterns.

## Setup

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt        # runtime only
pip install -r requirements-dev.txt     # runtime + test tooling (pytest, etc.)
```

Copy `.env.example` to `.env` and set `JWT_SECRET_KEY` before deploying this
anywhere public - without it, the server falls back to an insecure, publicly
known default key (fine for local dev only).

## Run

```
uvicorn main:app --reload
```

Server runs at `http://localhost:8000`.

## Try it

Open `client.html` directly in your browser (or serve it). Register/log in, enter a room name and connect from two tabs using the **same room**, and chat between them. Tabs using a *different* room won't see those messages.

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

Passwords must be 8-72 characters (72 is bcrypt's hard limit). `/register` and
`/login` are rate-limited (5/min and 10/min per IP by default) against
brute-force/spam registration.

## Message Protocol

All WebSocket messages are JSON. Client -> server:

```json
{"type": "chat", "content": "hello"}
{"type": "set_status", "status": "available" | "away" | "invisible"}
```

Server -> client:

```json
{"type": "chat", "username": "...", "content": "..."}
{"type": "system", "content": "..."}
{"type": "presence", "users": [{"username": "...", "status": "..."}]}
```

`presence` is a full roster snapshot (not an incremental diff) sent to everyone
in the room whenever someone joins, leaves, or changes their status - simplest
for clients to render since they can just replace their whole user grid each
time instead of tracking adds/removes themselves.

## Tests

```
pytest -v
```

A `conftest.py` fixture starts the server itself (on a dedicated test port,
against an isolated throwaway SQLite database) for the whole session - no
manual server startup needed, and it won't collide with a dev server you
might already have running for `client.html`. Same command runs in CI on
every push (see the badge above).

## Known limitations (by design, for a practice project)

- `ConnectionManager` state is in-memory in a single process - this can't
  horizontally scale (two instances wouldn't share room state). A real
  multi-instance deployment would need Redis pub/sub or similar.
- SQLite has the same single-instance ceiling; Postgres would be the
  production move.
- "Logout" is client-side only (clears the local token). JWTs can't be
  revoked without a token blocklist, so a leaked token stays valid until it
  expires (24h) regardless of logging out.

## Roadmap

- [x] MVP: single global chat room, broadcast to all connected clients
- [x] Rooms/channels: join a specific room, messages scoped to that room
- [x] Persistence: store message history in a database (SQLite), replayed to new joiners
- [x] Auth: JWT-based login, WebSocket identifies users from their token
- [x] Presence status: online-users grid with user-settable status (available/away/invisible)
- [x] Hardening pass: fixed a stored-XSS bug, env-var secret key, password validation, rate limiting, pytest + CI
- [ ] Stretch: typing indicators, private DMs
