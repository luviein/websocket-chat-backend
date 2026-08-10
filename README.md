# Chat Backend

[![Tests](https://github.com/luviein/websocket-chat-backend/actions/workflows/tests.yml/badge.svg)](https://github.com/luviein/websocket-chat-backend/actions/workflows/tests.yml)

A WebSocket-based chat backend built with FastAPI, for practicing real-time backend patterns.

## Project Structure

```
chat-backend/
├── main.py              # FastAPI app, routes, WebSocket endpoint
├── auth.py               # password hashing, JWT create/decode
├── database.py           # SQLite access (users, messages)
├── oauth.py               # Google OAuth client setup
├── static/
│   └── client.html        # minimal test client (served at /client.html)
├── tests/
│   ├── conftest.py         # spins the server up/down for the test session
│   ├── test_helpers.py     # shared test utilities
│   └── test_*.py            # one file per feature area
├── .github/workflows/tests.yml
├── requirements.txt        # runtime dependencies
├── requirements-dev.txt    # + test tooling
└── .env.example
```

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

Open [http://localhost:8000/client.html](http://localhost:8000/client.html) (the server now serves it directly - required for Google OAuth's redirect to work; opening the file directly still works fine for username/password login). Register/log in, enter a room name and connect from two tabs using the **same room**, and chat between them. Tabs using a *different* room won't see those messages.

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

### Google OAuth ("Sign in with Google")

Optional - the app works fine without it (the button just returns a clear
error instead of crashing). To enable it:

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and
   create a new project (or pick an existing one).
2. Under **APIs & Services > OAuth consent screen**, configure the consent
   screen (choose "External", fill in the required app name/support email -
   for local testing you can leave it in "Testing" publishing status and add
   your own Google account as a test user).
3. Under **APIs & Services > Credentials**, click **Create Credentials >
   OAuth client ID**, choose **Web application**.
4. Add this exact **Authorized redirect URI**:
   ```
   http://localhost:8000/auth/google/callback
   ```
   (must match exactly, including the path - Google rejects mismatches)
5. Save, then copy the generated **Client ID** and **Client Secret** into
   your local `.env` file as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
6. Restart the server. Clicking "Sign in with Google" in `client.html` (must
   be opened via `http://localhost:8000/client.html`, not `file://`, for the
   redirect back to work) will now walk through the real Google login flow.

First-time Google sign-in prompts the user to pick a **display name**
(instead of just showing their raw email in chat) before finishing account
creation - the pending email/google_id is held in a signed session cookie
between the OAuth redirect and that submission. The account itself is keyed
by the user's Google email with no local password set (they can only sign in
via Google afterward - a separate password-based registration with that same
email is rejected to avoid silently merging two identities). Returning users
skip the prompt and go straight through.

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

Every `username` field here is the resolved **display name** (see the Google
OAuth section above) - not necessarily the account's login identifier/email.

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
- [x] Google OAuth login, alongside the existing password-based accounts
- [ ] Stretch: typing indicators, private DMs

<img width="669" height="824" alt="image" src="https://github.com/user-attachments/assets/2235c011-142f-4b31-b78e-0610deda0c62" />
