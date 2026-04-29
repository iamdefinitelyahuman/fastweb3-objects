from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from .db import CacheDB, get_cache_db

RPC_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS rpc_cache (
    chain_id INTEGER NOT NULL,
    method TEXT NOT NULL,
    params_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (chain_id, method, params_json)
);
"""

_READ = object()
CacheRule = Callable[[Any, Any], Any | None]

ERC20_METADATA_SELECTORS = {
    "0x06fdde03",  # name()
    "0x95d89b41",  # symbol()
    "0x313ce567",  # decimals()
}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _eth_get_code_cache_params(call, result: Any = _READ) -> Any | None:
    params = getattr(call, "params", None)
    if not isinstance(params, (list, tuple)) or len(params) < 2:
        return None

    address, block = params[:2]
    if block != "latest":
        return None

    if result is not _READ and result == "0x":
        return None

    return [address.lower(), "latest"]


def _eth_call_cache_params(call, result: Any = _READ) -> Any | None:
    params = getattr(call, "params", None)
    if not isinstance(params, (list, tuple)) or len(params) < 2:
        return None

    tx, block = params[:2]
    if block != "latest":
        return None

    if not isinstance(tx, dict):
        return None

    to = tx.get("to")
    data = tx.get("data") or tx.get("input")

    if not isinstance(to, str) or not isinstance(data, str):
        return None

    data = data.lower()
    if data not in ERC20_METADATA_SELECTORS:
        return None

    if isinstance(result, Exception):
        return None

    return [to.lower(), data, "latest"]


CACHE_RULES: dict[str, CacheRule] = {
    "eth_getCode": _eth_get_code_cache_params,
    "eth_call": _eth_call_cache_params,
}


def cache_params(call, result: Any = _READ) -> Any | None:
    """Return normalized cache params for a call, if it is cacheable."""
    rule = CACHE_RULES.get(call.method)
    if rule is None:
        return None
    return rule(call, result)


class RpcCache:
    """Read and write raw RPC cache entries."""

    def __init__(self, db: CacheDB | None = None) -> None:
        """Initialize the RPC cache.

        Args:
            db: Cache database. If omitted, the process-global cache is used.
        """
        if db is None:
            db = get_cache_db()
        self.db = db

    def get(self, chain_id: int, method: str, params: Any) -> Any | None:
        """Return a cached RPC result, if present."""
        params_json = _json_dumps(params)
        row = self.db.execute(
            """
            SELECT result_json FROM rpc_cache
            WHERE chain_id = ? AND method = ? AND params_json = ?
            """,
            (int(chain_id), method, params_json),
        ).fetchone()

        if row is None:
            return None
        return json.loads(row["result_json"])

    def set(self, chain_id: int, method: str, params: Any, result: Any) -> None:
        """Store an RPC result."""
        now = int(time.time())
        params_json = _json_dumps(params)
        result_json = _json_dumps(result)
        self.db.execute(
            """
            INSERT INTO rpc_cache (
                chain_id, method, params_json, result_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(chain_id, method, params_json) DO UPDATE SET
                result_json = excluded.result_json,
                updated_at = excluded.updated_at
            """,
            (
                int(chain_id),
                method,
                params_json,
                result_json,
                now,
                now,
            ),
        )
        self.db.commit()


class RpcCacheMiddleware:
    """fastweb3 middleware that caches selected RPC responses."""

    def __init__(self, chain_id: int, db: CacheDB | None = None) -> None:
        """Initialize the middleware.

        Args:
            chain_id: Chain ID to use in cache keys.
            db: Cache database. If omitted, the process-global cache is used.
        """
        self.chain_id = int(chain_id)
        self.cache = RpcCache(db)

    def before_request(self, ctx, calls):
        """Remove cached calls from the outbound request list."""
        cached_results = {}
        miss_indexes = []
        miss_calls = []

        for idx, call in enumerate(calls):
            params = cache_params(call)

            if params is not None:
                result = self.cache.get(self.chain_id, call.method, params)
                if result is not None:
                    cached_results[idx] = result
                    continue

            miss_indexes.append(idx)
            miss_calls.append(call)

        ctx.state["rpc_cache_cached_results"] = cached_results
        ctx.state["rpc_cache_miss_indexes"] = miss_indexes
        ctx.state["rpc_cache_miss_calls"] = miss_calls
        ctx.state["rpc_cache_original_count"] = len(calls)

        return miss_calls

    def after_request(self, ctx, calls, results):
        """Store fresh results and merge them with cached hits."""
        cached_results = ctx.state.get("rpc_cache_cached_results", {})
        miss_indexes = ctx.state.get("rpc_cache_miss_indexes", [])
        miss_calls = ctx.state.get("rpc_cache_miss_calls", [])
        original_count = ctx.state.get("rpc_cache_original_count", len(results))

        merged = [None] * original_count

        for idx, result in cached_results.items():
            merged[idx] = result

        for idx, call, result in zip(miss_indexes, miss_calls, results, strict=True):
            params = cache_params(call, result)
            if params is not None:
                self.cache.set(self.chain_id, call.method, params, result)
            merged[idx] = result

        return merged
