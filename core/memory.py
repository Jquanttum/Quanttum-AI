import sqlite3
from pathlib import Path
from config import DATABASE_PATH

class Memory:
    def __init__(self):
        Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(DATABASE_PATH)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.connection.commit()

    def save(self, role, content):
        self.connection.execute(
            "INSERT INTO messages (role, content) VALUES (?, ?)",
            (role, content)
        )
        self.connection.commit()

    def recent(self, limit=30):
        cursor = self.connection.execute(
            "SELECT role, content, created_at FROM messages ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        return list(reversed(cursor.fetchall()))

    def close(self):
        self.connection.close()
