"""Internal SQLite cache storage used by fw3-objects."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from platformdirs import user_cache_dir


def _default_cache_path() -> Path:
    """Return the default SQLite cache path."""
    return Path(user_cache_dir("fw3-objects")) / "cache.sqlite"


_cache_db: CacheDB | None = None
_cache_db_lock = threading.Lock()


def get_cache_db() -> CacheDB:
    """Return the process-global cache database."""
    global _cache_db

    with _cache_db_lock:
        if _cache_db is None:
            _cache_db = CacheDB()
        return _cache_db


class CacheDB:
    """SQLite cache database."""

    def __init__(self) -> None:
        """Initialize the cache database."""
        self.path = _default_cache_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._configure()
        self._init_db()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        """Execute a SQL statement under the database lock."""
        with self._lock:
            return self._conn.execute(sql, params)

    def commit(self) -> None:
        """Commit the active transaction."""
        with self._lock:
            self._conn.commit()

    def _configure(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")

    def _init_db(self) -> None:
        with self._lock, self._conn:
            self._create_v1_schema()

    def _create_v1_schema(self) -> None:
        from .metadata import ADDRESS_METADATA_SCHEMA
        from .rpc import RPC_CACHE_SCHEMA

        self._conn.executescript("\n".join((RPC_CACHE_SCHEMA, ADDRESS_METADATA_SCHEMA)))
