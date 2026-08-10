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
    conn.commit()
    conn.close()


def create_user(username: str, hashed_password: str) -> bool:
    """Returns False if the username is already taken."""
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
    """Returns False if the username is already taken (e.g. by a password account)."""
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
