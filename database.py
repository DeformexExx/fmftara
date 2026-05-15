import sqlite3
import threading
import time
from typing import Any


class Database:
    def __init__(self, db_file: str = "aegis.db") -> None:
        self.db_file = db_file
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_file, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'offline',
                    ram REAL NOT NULL DEFAULT 0,
                    tcp_count INTEGER NOT NULL DEFAULT 0,
                    watchdog_enabled INTEGER NOT NULL DEFAULT 0,
                    saved_link TEXT DEFAULT '',
                    last_cookie TEXT DEFAULT '',
                    last_seen INTEGER NOT NULL DEFAULT 0,
                    screenshot_b64 TEXT DEFAULT '',
                    clipboard_last_read TEXT DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS console_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    stream TEXT NOT NULL,
                    line TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS command_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cookie_test_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    check_type TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    details TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.commit()

    def upsert_device(self, device_id: str, name: str) -> None:
        now = int(time.time())
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO devices(id, name, status, last_seen)
                VALUES(?, ?, 'online', ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    status = 'online',
                    last_seen = excluded.last_seen
                """,
                (device_id, name, now),
            )
            conn.commit()

    def update_heartbeat(self, device_id: str, ram: float, tcp_count: int, status: str = "online") -> None:
        now = int(time.time())
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE devices
                SET ram = ?, tcp_count = ?, status = ?, last_seen = ?
                WHERE id = ?
                """,
                (ram, tcp_count, status, now, device_id),
            )
            conn.commit()

    def set_watchdog(self, device_id: str, enabled: bool) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE devices SET watchdog_enabled = ? WHERE id = ?", (1 if enabled else 0, device_id))
            conn.commit()

    def set_link(self, device_id: str, link: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE devices SET saved_link = ? WHERE id = ?", (link.strip(), device_id))
            conn.commit()

    def set_cookie(self, device_id: str, cookie: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE devices SET last_cookie = ? WHERE id = ?", (cookie.strip(), device_id))
            conn.commit()

    def set_screenshot(self, device_id: str, screenshot_b64: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE devices SET screenshot_b64 = ? WHERE id = ?", (screenshot_b64, device_id))
            conn.commit()

    def set_clipboard_text(self, device_id: str, text: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE devices SET clipboard_last_read = ? WHERE id = ?", (text, device_id))
            conn.commit()

    def list_devices(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM devices ORDER BY name ASC").fetchall()
            return [dict(row) for row in rows]

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
            return dict(row) if row else None

    def mark_offline_stale(self, stale_after_seconds: int = 75) -> None:
        cutoff = int(time.time()) - stale_after_seconds
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE devices SET status = 'offline' WHERE last_seen < ?", (cutoff,))
            conn.commit()

    def queue_command(self, device_id: str, action: str, payload_json: str) -> int:
        now = int(time.time())
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO command_queue(device_id, action, payload, status, created_at, updated_at)
                VALUES (?, ?, ?, 'pending', ?, ?)
                """,
                (device_id, action, payload_json, now, now),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_pending_commands(self, device_id: str, limit: int = 25) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM command_queue
                WHERE device_id = ? AND status = 'pending'
                ORDER BY id ASC
                LIMIT ?
                """,
                (device_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def complete_command(self, command_id: int) -> None:
        now = int(time.time())
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE command_queue SET status = 'done', updated_at = ? WHERE id = ?",
                (now, command_id),
            )
            conn.commit()

    def append_console(self, device_id: str, stream: str, line: str) -> None:
        now = int(time.time())
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO console_logs(device_id, ts, stream, line) VALUES (?, ?, ?, ?)",
                (device_id, now, stream, line[-4000:]),
            )
            conn.commit()

    def get_console_tail(self, device_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM console_logs WHERE device_id = ? ORDER BY id DESC LIMIT ?
                ) ORDER BY id ASC
                """,
                (device_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def add_cookie_test_result(self, device_id: str, check_type: str, ok: bool, details: str) -> None:
        now = int(time.time())
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cookie_test_runs(device_id, ts, check_type, ok, details)
                VALUES (?, ?, ?, ?, ?)
                """,
                (device_id, now, check_type, 1 if ok else 0, details[:4000]),
            )
            conn.commit()

    def get_cookie_test_results(self, device_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM cookie_test_runs
                WHERE device_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (device_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]


db = Database()
