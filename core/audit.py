"""Append-only audit log for everything the Action Engine does or refuses.

Every request writes events under one request_id:
    requested -> denied | confirmation_asked -> confirmed | declined | expired
              -> succeeded | failed -> (later) undone

The table is protected by database triggers that reject UPDATE and DELETE,
so no bug in the application can quietly rewrite history.
"""

import json
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class AuditEvent:
    id: int
    created_at: str
    request_id: str
    action: str
    level: int
    event: str
    params: str
    detail: str
    undo: str
    message_id: int


def new_request_id():
    return uuid.uuid4().hex[:12]


class AuditLog:
    def __init__(self, connection):
        self.connection = connection

    def record(self, request_id, action, level, event, params=None,
               detail="", undo=None, message_id=None):
        self.connection.execute(
            """
            INSERT INTO audit_log (request_id, action, level, event, params,
                                   detail, undo, message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id, action, int(level), event,
                json.dumps(params, default=str) if params is not None else None,
                (detail or "")[:1000],
                json.dumps(undo, default=str) if undo is not None else None,
                message_id,
            )
        )
        self.connection.commit()

    @staticmethod
    def _event(row):
        return AuditEvent(
            row["id"], row["created_at"], row["request_id"], row["action"],
            row["level"], row["event"], row["params"] or "",
            row["detail"] or "", row["undo"] or "", row["message_id"],
        )

    def recent(self, limit=15):
        """The newest events first."""

        limit = max(1, min(int(limit), 200))
        rows = self.connection.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._event(row) for row in rows]

    def history(self, request_id):
        rows = self.connection.execute(
            "SELECT * FROM audit_log WHERE request_id = ? ORDER BY id",
            (request_id,)
        ).fetchall()
        return [self._event(row) for row in rows]

    def last_undoable(self):
        """The newest successful action that recorded how to undo it and
        has not been undone yet, or None."""

        row = self.connection.execute(
            """
            SELECT * FROM audit_log AS s
            WHERE s.event = 'succeeded' AND s.undo IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM audit_log AS u
                  WHERE u.request_id = s.request_id AND u.event = 'undone')
            ORDER BY s.id DESC LIMIT 1
            """
        ).fetchone()

        return self._event(row) if row else None
