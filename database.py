import os
import sqlite3
from pathlib import Path

# overridable so tests can point at an isolated, throwaway database instead
# of the real local dev chat.db
DB_PATH = Path(os.environ.get("CHAT_DB_PATH", Path(__file__).parent / "chat.db"))


def init_db():
    # NOTE: CREATE TABLE IF NOT EXISTS won't retroactively add new columns to
    # an existing local chat.db from before Google OAuth was added - delete
    # your local chat.db if you hit a "no such column: google_id" error.
    # No migration system for a project at this scale.
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id TEXT NOT NULL,
            username TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            hashed_password TEXT,
            google_id TEXT UNIQUE,
            display_name TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS blocks (
            blocker TEXT NOT NULL,
            blocked TEXT NOT NULL,
            PRIMARY KEY (blocker, blocked)
        )
        """
    )
    conn.commit()
    conn.close()


def is_display_name_taken(display_name: str) -> bool:
    """Password accounts use their own username as their effective display
    name (see get_display_name), so this checks both columns - needed so
    DMs and blocking (which address people by display name) always have an
    unambiguous target, since two different accounts showing up as the same
    name in chat would make both features silently misroute."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT 1 FROM users WHERE username = ? OR display_name = ? LIMIT 1",
        (display_name, display_name),
    ).fetchone()
    conn.close()
    return row is not None


def create_user(username: str, hashed_password: str) -> bool:
    """Returns False if the username is already taken, either literally or
    as another account's chosen display name (see is_display_name_taken)."""
    if is_display_name_taken(username):
        return False
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO users (username, hashed_password) VALUES (?, ?)",
            (username, hashed_password),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def create_google_user(username: str, google_id: str, display_name: str) -> bool:
    """Returns False if the account's username is already taken, or the
    chosen display_name collides with another account (see
    is_display_name_taken)."""
    if is_display_name_taken(display_name):
        return False
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO users (username, hashed_password, google_id, display_name) VALUES (?, NULL, ?, ?)",
            (username, google_id, display_name),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_user(username: str) -> sqlite3.Row | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT username, hashed_password, google_id, display_name FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    conn.close()
    return row


def get_user_by_google_id(google_id: str) -> sqlite3.Row | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT username, hashed_password, google_id, display_name FROM users WHERE google_id = ?",
        (google_id,),
    ).fetchone()
    conn.close()
    return row


def get_display_name(username: str) -> str:
    """Falls back to the account identifier itself (email or password-account
    username) if no display_name has been set - e.g. all password accounts,
    which use their chosen username as-is."""
    user = get_user(username)
    if user and user["display_name"]:
        return user["display_name"]
    return username


def save_message(room_id: str, username: str, content: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO messages (room_id, username, content) VALUES (?, ?, ?)",
        (room_id, username, content),
    )
    conn.commit()
    conn.close()


def get_recent_messages(room_id: str, limit: int = 50) -> list[sqlite3.Row]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT username, content, timestamp FROM messages "
        "WHERE room_id = ? ORDER BY id DESC LIMIT ?",
        (room_id, limit),
    ).fetchall()
    conn.close()
    return list(reversed(rows))  # oldest first, for correct chat order


def block_user(blocker: str, blocked: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR IGNORE INTO blocks (blocker, blocked) VALUES (?, ?)",
        (blocker, blocked),
    )
    conn.commit()
    conn.close()


def unblock_user(blocker: str, blocked: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM blocks WHERE blocker = ? AND blocked = ?", (blocker, blocked))
    conn.commit()
    conn.close()


def get_blocked_users(blocker: str) -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT blocked FROM blocks WHERE blocker = ?", (blocker,)).fetchall()
    conn.close()
    return [row[0] for row in rows]


def get_blockers_of(username: str) -> list[str]:
    """Who has blocked this username - used to silently withhold their
    messages in shared group rooms from those specific people, without
    restricting the sender themselves in any way (they keep posting/seeing
    the room normally, same as Discord-style blocking)."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT blocker FROM blocks WHERE blocked = ?", (username,)).fetchall()
    conn.close()
    return [row[0] for row in rows]


def is_blocked_pair(user_a: str, user_b: str) -> bool:
    """True if either user has blocked the other."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT 1 FROM blocks WHERE (blocker = ? AND blocked = ?) OR (blocker = ? AND blocked = ?) LIMIT 1",
        (user_a, user_b, user_b, user_a),
    ).fetchone()
    conn.close()
    return row is not None
