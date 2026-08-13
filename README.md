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
├── gemini.py               # /invite-gemini chat bot
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

### AI Bot ("/invite-gemini")

Optional, same graceful-fallback pattern as Google OAuth - without a key,
`/invite-gemini` just replies with a clear in-chat message instead of crashing.

1. Go to [Google AI Studio](https://aistudio.google.com/), sign in, and click
   **Get API key > Create API key**. Free tier, no credit card needed (exact
   rate limits are shown there and change over time, so not quoted here).
2. Add it to your local `.env`:
   ```
   GEMINI_API_KEY=your-key-here
   GEMINI_MODEL=gemini-flash-latest   # optional, this is already the default
   ```
   `GEMINI_MODEL` uses Google's "latest" alias rather than a pinned version on
   purpose - a pinned model ID (e.g. the original default here,
   `gemini-2.0-flash`) can and did get deprecated/removed by Google mid-project,
   breaking every call with a 404. Override this only if you specifically need
   a fixed model version.
3. Restart the server.

## Deploy (Render free tier)

`render.yaml` in the repo root is a [Render Blueprint](https://render.com/docs/blueprint-spec) -
it defines the whole service (build command, start command, env vars) so Render
can set it up from the repo in one step instead of manual dashboard config.

1. Push this repo to GitHub (already done if you're reading this from there).
2. In the [Render dashboard](https://dashboard.render.com/), click **New >
   Blueprint** and pick this repo. Render reads `render.yaml` and provisions a
   free web service from it. `JWT_SECRET_KEY` is auto-generated; leave
   `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/`GEMINI_API_KEY` blank if you're
   not using those features.
3. If you want Google OAuth working in production, add a second **Authorized
   redirect URI** in the Google Cloud Console credential from the OAuth
   section above:
   ```
   https://your-service-name.onrender.com/auth/google/callback
   ```
   then set `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` as env vars on the
   Render service (dashboard > Environment) and it'll redeploy.
4. If you want the Gemini bot working, set `GEMINI_API_KEY` the same way.

Two limits worth knowing about on the free tier:

- **No persistent disk** - `chat.db` (SQLite) resets on every deploy and on
  every restart after 15 minutes of inactivity. Fine for a portfolio demo,
  not for real data.
- **Spins down when idle** - the service sleeps after ~15 minutes with no
  requests and takes 30-60s to wake on the next one, dropping any open
  WebSocket connections in the meantime.

**Usage, from any chat room:**
- Type `/invite-gemini` to add it to the room - it shows up in the "Online
  Now" grid like any other participant, and everyone in the room sees a
  "Gemini has joined the room" notice. Typing `/` or `@` in the message box
  shows an autocomplete dropdown of matching commands/mentions, navigable
  with arrow keys and Enter (or a click) - a UX layer on top of the same
  server-side matching, not a separate protocol.
- Once invited, any message starting with `@gemini` (e.g. `@gemini what does
  this backend do?`) gets sent to the Gemini API, and its reply is broadcast
  back into the room as a normal chat message from "Gemini" - saved to
  history like anything else. Messages that don't start with `@gemini` are
  never sent to Gemini at all, and rooms that never used `/invite-gemini`
  ignore `@gemini` mentions completely (treated as plain text). After
  sending an `@gemini` message, the input refills with `@gemini ` for the
  next message too, so a back-and-forth conversation doesn't need retyping
  the mention every time - delete it manually to go back to a normal message.
- Gemini calls have a 30s timeout and always run off the server's main event
  loop (on a worker thread) - a slow or failed call can't freeze the rest of
  the server for other connections.

### Direct Messages & Blocking

A DM is just a regular room under the hood - `dm_room_id()` in `main.py`
computes a deterministic room id from the two participants' display names
(`dm:alice:bob`, always sorted so it's the same id no matter who initiates),
so DMs reuse the exact same connect/broadcast/persistence/presence code path
as a normal room, with no separate storage.

**Usage:** click **Message** next to someone in the "Online Now" grid (of any
room you already share with them) to open a private DM with just the two of
you - the header shows "DM with X" instead of "Room: X", and a **Back**
button appears to return to the room you came from. Click **Block** to stop
that person from DMing you: it immediately disconnects them if a DM is
already open (your own connection stays up, with a "You can't message this
user." notice sent to the one being kicked), and any future attempt by
either of you to open that DM is rejected before the connection even opens -
the client shows this as a quick popup instead of navigating into an empty
DM view first. **Unblock** reverses it.

Blocking is Discord-style, not mutual: it's a one-way filter on what *you*
see, invisible to the other side.
- **DMs**: neither side can start or continue a DM once either has blocked
  the other (this direction has to be mutual - a DM only has two people, so
  there's no "half-open" state that makes sense).
- **Shared group rooms**: blocking someone does *not* remove them from the
  room, hide their presence, or stop them from posting - it only makes the
  *blocker's* client stop receiving that person's `chat` messages (filtered
  server-side per-recipient in the broadcast). The blocked user keeps
  chatting normally, unaware anything changed, and everyone else in the room
  who hasn't blocked them still sees everything as usual.

The `dm:` prefix is reserved and can't be used as a free-text room name (the
client blocks it; the server would otherwise misinterpret it as a DM room
id). This only works because display names are required to be unique across
*all* accounts - both password usernames and Google-chosen display names
share one namespace (enforced in `database.py`'s `create_user`/
`create_google_user`) - otherwise two different people could collide into
the same DM room or block target.

### "Online Now" (account-wide) & DM Notifications

There's a single, collapsible "Online Now" panel, but what it shows depends
on where you are: inside a DM it's just the two of you (room-scoped
presence, like any normal room); everywhere else it's *every* display name
connected anywhere on the server right now (any room, any DM) - not just
people who happen to share your current room - so you can Message or Block
someone you've never been in a room with. This account-wide view updates
live off the same single WebSocket connection you're already using for
whatever room you're in; `ConnectionManager.broadcast_global()` in `main.py`
fans a `global_presence` snapshot out to literally every open connection
whenever anyone connects, disconnects, or changes status anywhere. (Gemini
is excluded from that snapshot - it's per-room bot presence, not a real
DM-able account - but the client still shows it here when it's active in
your current room, sourced from the normal room-scoped presence data.)

If someone DMs you while you're not actively looking at that DM, you'd
otherwise never know - `main.py` checks `manager.has_connection()` for the
DM room and, if you're not there, pushes a `dm_notification` to whatever
else you're connected to. The client shows this as a dismissible popup
("New message from X - click to open") *and* a small red dot next to that
person in the "Online Now" panel, so it's not just a fire-and-forget toast
you can miss - the dot persists until you actually open the DM (clicking
the toast, the red-dot entry, or its Message button all clear it the same
way, via `startDM()`).

## Message Protocol

All WebSocket messages are JSON. Client -> server:

```json
{"type": "chat", "content": "hello"}
{"type": "set_status", "status": "available" | "away" | "invisible"}
{"type": "typing"}
{"type": "block", "username": "..."}
{"type": "unblock", "username": "..."}
```

Server -> client:

```json
{"type": "chat", "username": "...", "content": "..."}
{"type": "system", "content": "..."}
{"type": "presence", "users": [{"username": "...", "status": "..."}]}
{"type": "typing", "username": "..."}
{"type": "gemini_typing"}
{"type": "self", "username": "..."}
{"type": "block_list", "blocked": ["..."]}
{"type": "dm_notification", "from": "..."}
{"type": "global_presence", "users": [{"username": "...", "status": "..."}]}
```

Every `username` field here is the resolved **display name** (see the Google
OAuth section above) - not necessarily the account's login identifier/email.

`presence` is a full roster snapshot (not an incremental diff) sent to everyone
in the room whenever someone joins, leaves, or changes their status - simplest
for clients to render since they can just replace their whole user grid each
time instead of tracking adds/removes themselves.

`typing` has no explicit "stopped typing" counterpart in either direction -
the client throttles how often it sends one while the user types, and
whoever receives it just auto-expires the "X is typing..." indicator a few
seconds after the last one arrives (or immediately, once that user's actual
chat message shows up). `gemini_typing` is broadcast right before the
(possibly 30s-long) Gemini API call starts, so the room sees "Gemini is
typing..." instead of a silent gap; it's cleared once Gemini's `chat` reply
arrives.

`self`, `block_list`, and `global_presence` are all sent once, right after
connecting to any room - `self` tells the client its own resolved display
name (needed to compute DM room ids and to hide Message/Block buttons on
itself), `block_list` is the current user's full block list (to render Block
vs. Unblock), `global_presence` is the initial "Online Everywhere" snapshot.
`block_list` is re-sent after every `block`/`unblock`, and `global_presence`
is re-broadcast to *every* connected client (not just the one room) whenever
anyone connects, disconnects, or changes status anywhere - both so the
client's state stays in sync with the server rather than updating
optimistically client-side.

`dm_notification` is sent to a DM recipient's *other* active connections
(whatever room/DM they're actually looking at) whenever they get a new DM
they're not currently viewing live - otherwise a DM sent while the recipient
is elsewhere would just silently vanish into a room they haven't opened. The
client shows it as a dismissible popup that jumps straight into that DM, and
marks a red dot on that person in the "Online Everywhere" list until it's
opened.

## Tests

```
pytest -v
```

A `conftest.py` fixture starts the server itself (on a dedicated test port,
against an isolated throwaway SQLite database) for the whole session - no
manual server startup needed, and it won't collide with a dev server you
might already have running for `client.html`. Same command runs in CI on
every push (see the badge above).

The Gemini tests never call the real API - `conftest.py` passes the test
server a deliberately fake key *and* points `GEMINI_BASE_URL` at an
unreachable local address (`http://127.0.0.1:1`), so the invite/mention flow
is tested end-to-end with a failure that's instant and 100% deterministic,
independent of Google's real servers, network conditions, or which model
`GEMINI_MODEL` happens to resolve to.

`pytest-timeout` (60s default, `timeout_method = thread`, see `pytest.ini`)
is a safety net from learning this the hard way: an early version of the
Gemini integration blocked the *entire* server's event loop on a slow API
call, hanging every other connection - not just the one that triggered it -
until the call gave up on its own. `asyncio.wait_for` alone didn't fix it
(cancellation only works at a cooperative yield point, and the blocking call
never yielded one); moving the call onto a worker thread via
`asyncio.to_thread` did.

Also worth knowing if a test only fails in CI: `conftest.py` explicitly adds
the project root to `sys.path`, since `python -m pytest` (used in every
local run here) implicitly does this, but CI's plain `pytest -v` doesn't -
without it, root-level module imports (`import gemini`, etc.) work locally
and fail in CI with a `ModuleNotFoundError`, for reasons that don't reproduce
outside CI's exact invocation.

## Known limitations (by design, for a practice project)

- `ConnectionManager` state is in-memory in a single process - this can't
  horizontally scale (two instances wouldn't share room state). A real
  multi-instance deployment would need Redis pub/sub or similar.
- SQLite has the same single-instance ceiling; Postgres would be the
  production move.
- "Logout" is client-side only (clears the local token). JWTs can't be
  revoked without a token blocklist, so a leaked token stays valid until it
  expires (24h) regardless of logging out.
- Blocking doesn't retroactively filter chat history - if you block someone
  after they've already posted in a room, their past messages you already
  received (or that get replayed from history on a future join) aren't
  hidden, only their *new* messages going forward.

## Roadmap

- [x] MVP: single global chat room, broadcast to all connected clients
- [x] Rooms/channels: join a specific room, messages scoped to that room
- [x] Persistence: store message history in a database (SQLite), replayed to new joiners
- [x] Auth: JWT-based login, WebSocket identifies users from their token
- [x] Presence status: online-users grid with user-settable status (available/away/invisible)
- [x] Hardening pass: fixed a stored-XSS bug, env-var secret key, password validation, rate limiting, pytest + CI
- [x] Google OAuth login, alongside the existing password-based accounts
- [x] AI bot: `/invite-gemini` + `@gemini` mentions, backed by the Gemini API
- [x] Typing indicators: "X is typing..." for users, "Gemini is typing..." while an AI reply is in flight
- [x] Private DMs, plus the ability to block a user (see Direct Messages & Blocking above)

<img width="984" height="865" alt="image" src="https://github.com/user-attachments/assets/86967f43-cea6-4e3c-8f27-28dc5d51f0a1" />

