from __future__ import annotations

import pytest

from fw3_objects.cache.db import CacheDB
from fw3_objects.cache.metadata import AddressMetadataCache
from fw3_objects.cache.rpc import RpcCache, RpcCacheMiddleware, cache_params


class DummyCall:
    def __init__(self, method: str, params=None) -> None:
        self.method = method
        self.params = params


class DummyContext:
    def __init__(self) -> None:
        self.state = {}


@pytest.fixture
def db(monkeypatch, tmp_path):
    path = tmp_path / "cache.sqlite"
    monkeypatch.setattr("fw3_objects.cache.db._default_cache_path", lambda: path)
    db = CacheDB()
    try:
        yield db
    finally:
        db.close()


def test_cache_db_creates_v1_schema(db) -> None:
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()

    assert [row["name"] for row in rows] == ["address_metadata", "rpc_cache"]


def test_rpc_cache_get_set_and_overwrite(db) -> None:
    cache = RpcCache(db)
    params = ["0x" + "aa" * 20, "latest"]

    assert cache.get(1, "eth_getCode", params) is None

    cache.set(1, "eth_getCode", params, "0x1234")
    assert cache.get(1, "eth_getCode", params) == "0x1234"

    cache.set(1, "eth_getCode", params, "0xabcd")
    assert cache.get(1, "eth_getCode", params) == "0xabcd"


def test_rpc_cache_keys_params_with_stable_json(db) -> None:
    cache = RpcCache(db)

    cache.set(1, "method", {"b": 2, "a": 1}, {"ok": True})

    assert cache.get(1, "method", {"a": 1, "b": 2}) == {"ok": True}


def test_address_metadata_merges_document_keys(db) -> None:
    cache = AddressMetadataCache(db)
    address = "0x" + "AA" * 20

    cache.set(1, address, "abi", [{"type": "function"}])
    cache.set(1, address, "name", "Token")

    assert cache.get(1, address.lower(), "abi") == [{"type": "function"}]
    assert cache.get(1, address, "name") == "Token"
    assert cache.get(1, address, "missing") is None


def test_eth_get_code_cache_params_only_cache_latest_non_empty_code() -> None:
    call = DummyCall("eth_getCode", ["0x" + "AA" * 20, "latest"])

    assert cache_params(call) == ["0x" + "aa" * 20, "latest"]
    assert cache_params(call, "0x1234") == ["0x" + "aa" * 20, "latest"]
    assert cache_params(call, "0x") is None
    assert cache_params(DummyCall("eth_getCode", ["0x" + "AA" * 20, "pending"])) is None
    assert cache_params(DummyCall("eth_blockNumber", [])) is None


@pytest.mark.parametrize("params", [None, [], ["0x" + "AA" * 20]])
def test_eth_get_code_cache_params_rejects_missing_params(params) -> None:
    assert cache_params(DummyCall("eth_getCode", params)) is None


def test_rpc_cache_middleware_drops_cached_calls_and_merges_results(db) -> None:
    cache = RpcCache(db)
    cached_call = DummyCall("eth_getCode", ["0x" + "11" * 20, "latest"])
    miss_call = DummyCall("eth_getCode", ["0x" + "22" * 20, "latest"])
    uncached_call = DummyCall("eth_blockNumber", [])
    cache.set(1, cached_call.method, ["0x" + "11" * 20, "latest"], "0xcached")
    middleware = RpcCacheMiddleware(1, db)
    ctx = DummyContext()

    outbound = middleware.before_request(ctx, [cached_call, miss_call, uncached_call])
    merged = middleware.after_request(ctx, outbound, ["0xmiss", "0x10"])

    assert outbound == [miss_call, uncached_call]
    assert merged == ["0xcached", "0xmiss", "0x10"]
    assert cache.get(1, miss_call.method, ["0x" + "22" * 20, "latest"]) == "0xmiss"


def test_rpc_cache_middleware_does_not_store_empty_code_results(db) -> None:
    call = DummyCall("eth_getCode", ["0x" + "33" * 20, "latest"])
    middleware = RpcCacheMiddleware(1, db)
    ctx = DummyContext()

    outbound = middleware.before_request(ctx, [call])
    merged = middleware.after_request(ctx, outbound, ["0x"])

    assert merged == ["0x"]
    assert RpcCache(db).get(1, call.method, ["0x" + "33" * 20, "latest"]) is None
