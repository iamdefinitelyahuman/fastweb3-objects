from __future__ import annotations

import json
import time
from typing import Any

from .db import CacheDB, get_cache_db

ADDRESS_METADATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS address_metadata (
    chain_id INTEGER NOT NULL,
    address TEXT NOT NULL,
    document_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (chain_id, address)
);
"""


def _normalize_address(address: str) -> str:
    return address.lower()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class AddressMetadataCache:
    """Read and write cached metadata about contract addresses."""

    def __init__(self, db: CacheDB | None = None) -> None:
        """Initialize the address metadata cache.

        Args:
            db: Cache database. If omitted, the process-global cache is used.
        """
        if db is None:
            db = get_cache_db()
        self.db = db

    def get(self, chain_id: int, address: str, key: str) -> Any | None:
        """Return cached metadata for an address key, if present."""
        document = self._get_document(chain_id, address)
        return document.get(key)

    def set(self, chain_id: int, address: str, key: str, value: Any) -> None:
        """Store metadata for an address key."""
        now = int(time.time())
        address = _normalize_address(address)

        with self.db._lock, self.db._conn:
            row = self.db._conn.execute(
                """
                SELECT document_json FROM address_metadata
                WHERE chain_id = ? AND address = ?
                """,
                (int(chain_id), address),
            ).fetchone()
            document = {} if row is None else json.loads(row["document_json"])
            document[key] = value
            document_json = _json_dumps(document)

            self.db._conn.execute(
                """
                INSERT INTO address_metadata (
                    chain_id, address, document_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chain_id, address) DO UPDATE SET
                    document_json = excluded.document_json,
                    updated_at = excluded.updated_at
                """,
                (int(chain_id), address, document_json, now, now),
            )

    def _get_document(self, chain_id: int, address: str) -> dict[str, Any]:
        row = self.db.execute(
            """
            SELECT document_json FROM address_metadata
            WHERE chain_id = ? AND address = ?
            """,
            (int(chain_id), _normalize_address(address)),
        ).fetchone()

        if row is None:
            return {}
        return json.loads(row["document_json"])
