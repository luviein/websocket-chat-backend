import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "chat.db"


def init_db():
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
    conn.commit()
    conn.close()


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
