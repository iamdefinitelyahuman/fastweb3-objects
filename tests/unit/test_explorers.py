from __future__ import annotations

import httpx
import pytest

from fw3_objects.errors import (
    ABINotFound,
    ExplorerConnectionError,
    ExplorerError,
    ExplorerRateLimited,
)
from fw3_objects.explorers import abi as abi_lookup
from fw3_objects.explorers import blockscout, etherscan

ADDRESS = "0x" + "11" * 20
ABI = [{"type": "function", "name": "foo", "inputs": [], "outputs": []}]


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200, headers=None, json_error=None) -> None:
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.json_error = json_error

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.invalid")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.fixture(autouse=True)
def reset_abi_lookup_state(monkeypatch):
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    monkeypatch.delenv("ETHERSCAN_TOKEN", raising=False)
    monkeypatch.delenv("BLOCKSCOUT_API_KEY", raising=False)
    abi_lookup._pending.clear()
    abi_lookup._negative_cache.clear()
    abi_lookup._rate_limited_until.clear()
    yield
    abi_lookup._pending.clear()
    abi_lookup._negative_cache.clear()
    abi_lookup._rate_limited_until.clear()


def test_etherscan_get_abi_parses_success_response(monkeypatch) -> None:
    calls = []

    def fake_get(url, *, params, timeout):
        calls.append((url, params, timeout))
        return FakeResponse({"status": "1", "result": '[{"type":"function","name":"foo"}]'})

    monkeypatch.setattr(etherscan.httpx, "get", fake_get)

    assert etherscan.get_abi(1, ADDRESS, "key") == [{"type": "function", "name": "foo"}]
    assert calls == [
        (
            etherscan.BASE_URL,
            {
                "chainid": 1,
                "module": "contract",
                "action": "getabi",
                "address": ADDRESS,
                "apikey": "key",
            },
            10,
        )
    ]


def test_etherscan_get_abi_maps_error_responses(monkeypatch) -> None:
    monkeypatch.setattr(
        etherscan.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse(status_code=429, headers={"Retry-After": "1.5"}),
    )
    with pytest.raises(ExplorerRateLimited) as excinfo:
        etherscan.get_abi(1, ADDRESS, "key")
    assert excinfo.value.provider == "etherscan"
    assert excinfo.value.retry_after == 1.5

    monkeypatch.setattr(
        etherscan.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse({"status": "0", "message": "rate limit"}),
    )
    with pytest.raises(ExplorerRateLimited):
        etherscan.get_abi(1, ADDRESS, "key")

    monkeypatch.setattr(
        etherscan.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse(
            {"status": "0", "result": "Contract source code not verified"}
        ),
    )
    with pytest.raises(ABINotFound, match="not verified"):
        etherscan.get_abi(1, ADDRESS, "key")


def test_etherscan_get_abi_maps_connection_and_invalid_payload(monkeypatch) -> None:
    def raise_connect_error(*args, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(etherscan.httpx, "get", raise_connect_error)
    with pytest.raises(ExplorerConnectionError):
        etherscan.get_abi(1, ADDRESS, "key")

    monkeypatch.setattr(
        etherscan.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse(json_error=ValueError("bad json")),
    )
    with pytest.raises(ExplorerError, match="Invalid Etherscan response"):
        etherscan.get_abi(1, ADDRESS, "key")

    monkeypatch.setattr(
        etherscan.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse({"status": "1", "result": "not json"}),
    )
    with pytest.raises(ExplorerError, match="Invalid Etherscan ABI JSON"):
        etherscan.get_abi(1, ADDRESS, "key")

    monkeypatch.setattr(
        etherscan.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse({"status": "1", "result": "{}"}),
    )
    with pytest.raises(ExplorerError, match="Invalid Etherscan ABI"):
        etherscan.get_abi(1, ADDRESS, "key")


def test_blockscout_get_abi_accepts_string_or_list_result(monkeypatch) -> None:
    monkeypatch.setattr(
        blockscout.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse({"status": "1", "result": '[{"type":"function"}]'}),
    )
    assert blockscout.get_abi(1, ADDRESS, "key") == [{"type": "function"}]

    monkeypatch.setattr(
        blockscout.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse({"status": "1", "result": [{"type": "event"}]}),
    )
    assert blockscout.get_abi(1, ADDRESS, "key") == [{"type": "event"}]


def test_fetch_abi_uses_configured_providers_and_falls_back(monkeypatch) -> None:
    calls = []

    def etherscan_get_abi(chain_id, address, api_key):
        calls.append(("etherscan", chain_id, address, api_key))
        raise ABINotFound("not verified")

    def blockscout_get_abi(chain_id, address, api_key):
        calls.append(("blockscout", chain_id, address, api_key))
        return ABI

    monkeypatch.setenv("ETHERSCAN_TOKEN", "etherscan-key")
    monkeypatch.setenv("BLOCKSCOUT_API_KEY", "blockscout-key")
    monkeypatch.setattr(abi_lookup.etherscan, "get_abi", etherscan_get_abi)
    monkeypatch.setattr(abi_lookup.blockscout, "get_abi", blockscout_get_abi)

    assert abi_lookup._fetch_abi(1, ADDRESS) == ABI
    assert calls == [
        ("etherscan", 1, ADDRESS, "etherscan-key"),
        ("blockscout", 1, ADDRESS, "blockscout-key"),
    ]


def test_fetch_abi_without_api_keys_uses_api_key_name_in_error_message() -> None:
    with pytest.raises(ABINotFound, match="ETHERSCAN_API_KEY"):
        abi_lookup._fetch_abi(1, ADDRESS)


def test_run_job_only_negative_caches_not_found_errors(monkeypatch) -> None:
    not_found = abi_lookup.ABILookupJob(1, ADDRESS)
    abi_lookup._pending[(1, ADDRESS)] = not_found
    monkeypatch.setattr(
        abi_lookup,
        "_fetch_abi",
        lambda *args: (_ for _ in ()).throw(ABINotFound("nope")),
    )

    abi_lookup._run_job(not_found)

    assert not_found.done
    assert (1, ADDRESS) in abi_lookup._negative_cache
    assert (1, ADDRESS) not in abi_lookup._pending
    with pytest.raises(ABINotFound):
        not_found.wait()

    connection_error = abi_lookup.ABILookupJob(1, ADDRESS)
    abi_lookup._pending[(1, ADDRESS)] = connection_error
    abi_lookup._negative_cache.clear()
    monkeypatch.setattr(
        abi_lookup,
        "_fetch_abi",
        lambda *args: (_ for _ in ()).throw(ExplorerConnectionError("offline")),
    )

    abi_lookup._run_job(connection_error)

    assert (1, ADDRESS) not in abi_lookup._negative_cache
    assert (1, ADDRESS) not in abi_lookup._pending
    with pytest.raises(ExplorerConnectionError):
        connection_error.wait()


def test_blockscout_get_abi_sends_expected_request_params(monkeypatch) -> None:
    calls = []

    def fake_get(url, *, params, timeout):
        calls.append((url, params, timeout))
        return FakeResponse({"status": "1", "result": '[{"type":"function","name":"foo"}]'})

    monkeypatch.setattr(blockscout.httpx, "get", fake_get)

    assert blockscout.get_abi("1", ADDRESS, "key") == [{"type": "function", "name": "foo"}]
    assert calls == [
        (
            blockscout.BASE_URL,
            {
                "chain_id": 1,
                "module": "contract",
                "action": "getabi",
                "address": ADDRESS,
                "apikey": "key",
            },
            10,
        )
    ]


@pytest.mark.parametrize(
    ("header", "expected"),
    [("1.5", 1.5), ("-1", 0.0), ("soon", None), (None, None)],
)
def test_blockscout_http_429_raises_rate_limited_with_retry_after(
    monkeypatch, header, expected
) -> None:
    headers = {} if header is None else {"Retry-After": header}
    monkeypatch.setattr(
        blockscout.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse(status_code=429, headers=headers),
    )

    with pytest.raises(ExplorerRateLimited) as excinfo:
        blockscout.get_abi(1, ADDRESS, "key")

    assert excinfo.value.provider == "blockscout"
    assert excinfo.value.retry_after == expected


def test_blockscout_connection_errors_raise_connection_error(monkeypatch) -> None:
    def fake_get(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(blockscout.httpx, "get", fake_get)

    with pytest.raises(ExplorerConnectionError, match="Could not connect to Blockscout"):
        blockscout.get_abi(1, ADDRESS, "key")


def test_blockscout_non_429_http_errors_raise_explorer_error(monkeypatch) -> None:
    monkeypatch.setattr(
        blockscout.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse(status_code=500),
    )

    with pytest.raises(ExplorerError, match="boom"):
        blockscout.get_abi(1, ADDRESS, "key")


def test_blockscout_invalid_json_response_raises_explorer_error(monkeypatch) -> None:
    monkeypatch.setattr(
        blockscout.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse(json_error=ValueError("bad json")),
    )

    with pytest.raises(ExplorerError, match="Invalid Blockscout response"):
        blockscout.get_abi(1, ADDRESS, "key")


@pytest.mark.parametrize("message", ["rate limit", "RATE LIMIT exceeded"])
def test_blockscout_status_zero_rate_limit_raises_rate_limited(monkeypatch, message) -> None:
    monkeypatch.setattr(
        blockscout.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse({"status": "0", "message": message}),
    )

    with pytest.raises(ExplorerRateLimited) as excinfo:
        blockscout.get_abi(1, ADDRESS, "key")

    assert excinfo.value.provider == "blockscout"
    assert excinfo.value.retry_after is None


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"status": "0", "result": "Contract source code not verified"}, "not verified"),
        ({"status": "0", "message": "NOTOK"}, "NOTOK"),
        ({"status": "0"}, "ABI not found"),
        ({"status": "1", "result": ""}, "ABI not found"),
        ({"status": "1", "result": None}, "ABI not found"),
    ],
)
def test_blockscout_not_found_response_shapes_raise_abi_not_found(
    monkeypatch, payload, match
) -> None:
    monkeypatch.setattr(
        blockscout.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse(payload),
    )

    with pytest.raises(ABINotFound, match=match):
        blockscout.get_abi(1, ADDRESS, "key")


def test_blockscout_accepts_abi_as_json_string_or_list(monkeypatch) -> None:
    monkeypatch.setattr(
        blockscout.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse({"status": "1", "result": '[{"type":"function"}]'}),
    )
    assert blockscout.get_abi(1, ADDRESS, "key") == [{"type": "function"}]

    monkeypatch.setattr(
        blockscout.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse({"status": "1", "result": [{"type": "event"}]}),
    )
    assert blockscout.get_abi(1, ADDRESS, "key") == [{"type": "event"}]


@pytest.mark.parametrize(
    ("result", "match"),
    [
        ("not json", "Invalid Blockscout ABI JSON"),
        ({"type": "function"}, "Invalid Blockscout ABI"),
        (["not a dict"], "Invalid Blockscout ABI"),
        ([{"type": "function"}, "not a dict"], "Invalid Blockscout ABI"),
        (123, "Invalid Blockscout ABI"),
    ],
)
def test_blockscout_invalid_abi_payloads_raise_explorer_error(monkeypatch, result, match) -> None:
    monkeypatch.setattr(
        blockscout.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse({"status": "1", "result": result}),
    )

    with pytest.raises(ExplorerError, match=match):
        blockscout.get_abi(1, ADDRESS, "key")


def test_abi_lookup_job_callbacks_before_and_after_completion() -> None:
    job = abi_lookup.ABILookupJob(1, ADDRESS)
    calls = []

    job.add_done_callback(calls.append)
    job.set_result(ABI)
    job.add_done_callback(calls.append)

    assert calls == [ABI, ABI]
    assert job.done
    assert job.wait() == ABI


def test_abi_lookup_job_ignores_callback_exceptions() -> None:
    job = abi_lookup.ABILookupJob(1, ADDRESS)
    calls = []

    def broken_callback(_abi):
        calls.append("called")
        raise RuntimeError("boom")

    job.add_done_callback(broken_callback)
    job.set_result(ABI)

    assert calls == ["called"]
    assert job.wait() == ABI


def test_abi_lookup_job_set_error_clears_callbacks_and_wait_raises() -> None:
    job = abi_lookup.ABILookupJob(1, ADDRESS)
    calls = []
    error = ABINotFound("missing")

    job.add_done_callback(calls.append)
    job.set_error(error)

    assert calls == []
    assert job.done
    with pytest.raises(ABINotFound, match="missing"):
        job.wait()


def test_abi_lookup_job_bump_priority_requeues_only_when_priority_improves(monkeypatch) -> None:
    job = abi_lookup.ABILookupJob(1, ADDRESS, priority=abi_lookup.LOW_PRIORITY)
    enqueued = []
    monkeypatch.setattr(abi_lookup, "_enqueue", enqueued.append)

    job.bump_priority(abi_lookup.LOW_PRIORITY)
    assert enqueued == []

    job.bump_priority(abi_lookup.HIGH_PRIORITY)
    assert job.priority == abi_lookup.HIGH_PRIORITY
    assert enqueued == [job]

    job.set_result(ABI)
    job.bump_priority(-1)
    assert enqueued == [job]


def test_fetch_abi_reuses_pending_job_adds_callback_and_bumps_priority(monkeypatch) -> None:
    enqueued = []
    callbacks = []
    monkeypatch.setattr(abi_lookup, "_ensure_worker_started", lambda: None)
    monkeypatch.setattr(abi_lookup, "_enqueue", enqueued.append)

    first = abi_lookup.fetch_abi(1, ADDRESS, priority=abi_lookup.LOW_PRIORITY)
    second = abi_lookup.fetch_abi(
        1,
        ADDRESS.upper(),
        priority=abi_lookup.HIGH_PRIORITY,
        on_success=callbacks.append,
    )

    assert second is first
    assert first.priority == abi_lookup.HIGH_PRIORITY
    assert enqueued == [first, first]

    first.set_result(ABI)
    assert callbacks == [ABI]


def test_fetch_abi_ignore_negative_cache_creates_separate_pending_job(monkeypatch) -> None:
    enqueued = []
    monkeypatch.setattr(abi_lookup, "_ensure_worker_started", lambda: None)
    monkeypatch.setattr(abi_lookup, "_enqueue", enqueued.append)

    first = abi_lookup.fetch_abi(1, ADDRESS)
    second = abi_lookup.fetch_abi(1, ADDRESS, ignore_negative_cache=True)

    assert second is not first
    assert second.ignore_negative_cache
    assert abi_lookup._pending[(1, ADDRESS)] is second
    assert enqueued == [first, second]


def test_fetch_abi_returns_failed_job_for_live_negative_cache(monkeypatch) -> None:
    monkeypatch.setattr(abi_lookup.time, "monotonic", lambda: 10.0)
    abi_lookup._negative_cache[(1, ADDRESS)] = 20.0

    job = abi_lookup.fetch_abi(1, ADDRESS)

    assert job.done
    with pytest.raises(ABINotFound, match="ABI not found"):
        job.wait()


def test_fetch_abi_expires_stale_negative_cache_and_enqueues(monkeypatch) -> None:
    enqueued = []
    monkeypatch.setattr(abi_lookup.time, "monotonic", lambda: 30.0)
    monkeypatch.setattr(abi_lookup, "_ensure_worker_started", lambda: None)
    monkeypatch.setattr(abi_lookup, "_enqueue", enqueued.append)
    abi_lookup._negative_cache[(1, ADDRESS)] = 20.0

    job = abi_lookup.fetch_abi(1, ADDRESS)

    assert job is abi_lookup._pending[(1, ADDRESS)]
    assert (1, ADDRESS) not in abi_lookup._negative_cache
    assert enqueued == [job]


def test_ensure_worker_started_only_starts_once(monkeypatch) -> None:
    starts = []

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            starts.append((target, name, daemon))

        def start(self):
            starts.append("started")

    monkeypatch.setattr(abi_lookup.threading, "Thread", FakeThread)

    abi_lookup._ensure_worker_started()
    abi_lookup._ensure_worker_started()

    assert starts == [(abi_lookup._worker, "fw3-objects-abi-lookup", True), "started"]


def test_enqueue_orders_by_priority_then_sequence() -> None:
    low = abi_lookup.ABILookupJob(1, ADDRESS, priority=abi_lookup.LOW_PRIORITY)
    high = abi_lookup.ABILookupJob(1, ADDRESS, priority=abi_lookup.HIGH_PRIORITY)

    abi_lookup._enqueue(low)
    abi_lookup._enqueue(high)

    assert abi_lookup._queue.get()[2] is high
    assert abi_lookup._queue.get()[2] is low


def test_worker_skips_done_and_stale_priority_jobs(monkeypatch) -> None:
    done_job = abi_lookup.ABILookupJob(1, ADDRESS)
    done_job.set_result(ABI)
    stale_job = abi_lookup.ABILookupJob(1, "0x" + "22" * 20, priority=abi_lookup.HIGH_PRIORITY)
    calls = []

    monkeypatch.setattr(abi_lookup, "_run_job", calls.append)

    abi_lookup._queue.put((done_job.priority, 0, done_job))
    abi_lookup._queue.put((abi_lookup.LOW_PRIORITY, 1, stale_job))

    original_get = abi_lookup._queue.get

    def stop_after_two():
        if abi_lookup._queue.empty():
            raise KeyboardInterrupt
        return original_get()

    monkeypatch.setattr(abi_lookup._queue, "get", stop_after_two)

    with pytest.raises(KeyboardInterrupt):
        abi_lookup._worker()

    assert calls == []


def test_worker_runs_current_pending_job(monkeypatch) -> None:
    job = abi_lookup.ABILookupJob(1, ADDRESS)
    calls = []

    monkeypatch.setattr(abi_lookup, "_run_job", calls.append)
    abi_lookup._queue.put((job.priority, 0, job))

    original_get = abi_lookup._queue.get

    def stop_after_one():
        if abi_lookup._queue.empty():
            raise KeyboardInterrupt
        return original_get()

    monkeypatch.setattr(abi_lookup._queue, "get", stop_after_one)

    with pytest.raises(KeyboardInterrupt):
        abi_lookup._worker()

    assert calls == [job]


def test_run_job_sets_result_removes_pending_and_does_not_negative_cache(monkeypatch) -> None:
    job = abi_lookup.ABILookupJob(1, ADDRESS)
    abi_lookup._pending[(1, ADDRESS)] = job
    monkeypatch.setattr(abi_lookup, "_fetch_abi", lambda chain_id, address: ABI)

    abi_lookup._run_job(job)

    assert job.done
    assert job.wait() == ABI
    assert (1, ADDRESS) not in abi_lookup._pending
    assert (1, ADDRESS) not in abi_lookup._negative_cache


def test_run_job_negative_caches_abi_not_found_errors(monkeypatch) -> None:
    job = abi_lookup.ABILookupJob(1, ADDRESS)
    abi_lookup._pending[(1, ADDRESS)] = job
    monkeypatch.setattr(abi_lookup.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        abi_lookup,
        "_fetch_abi",
        lambda chain_id, address: (_ for _ in ()).throw(ABINotFound("missing")),
    )

    abi_lookup._run_job(job)

    assert job.done
    assert abi_lookup._negative_cache[(1, ADDRESS)] == 100.0 + abi_lookup.NEGATIVE_ABI_TTL
    assert (1, ADDRESS) not in abi_lookup._pending
    with pytest.raises(ABINotFound, match="missing"):
        job.wait()


def test_run_job_does_not_negative_cache_ignored_or_non_pending_jobs(monkeypatch) -> None:
    ignored = abi_lookup.ABILookupJob(1, ADDRESS, ignore_negative_cache=True)
    non_pending = abi_lookup.ABILookupJob(1, "0x" + "22" * 20)
    monkeypatch.setattr(
        abi_lookup,
        "_fetch_abi",
        lambda chain_id, address: (_ for _ in ()).throw(ABINotFound("missing")),
    )

    abi_lookup._pending[(1, ADDRESS)] = ignored
    abi_lookup._run_job(ignored)
    abi_lookup._run_job(non_pending)

    assert abi_lookup._negative_cache == {}


def test_run_job_does_not_negative_cache_connection_errors(monkeypatch) -> None:
    job = abi_lookup.ABILookupJob(1, ADDRESS)
    abi_lookup._pending[(1, ADDRESS)] = job
    monkeypatch.setattr(
        abi_lookup,
        "_fetch_abi",
        lambda chain_id, address: (_ for _ in ()).throw(ExplorerConnectionError("offline")),
    )

    abi_lookup._run_job(job)

    assert abi_lookup._negative_cache == {}
    with pytest.raises(ExplorerConnectionError, match="offline"):
        job.wait()


def test_providers_reads_env_keys_and_prefers_etherscan_api_key(monkeypatch) -> None:
    monkeypatch.setenv("ETHERSCAN_API_KEY", "api-key")
    monkeypatch.setenv("ETHERSCAN_TOKEN", "token-key")
    monkeypatch.setenv("BLOCKSCOUT_API_KEY", "blockscout-key")

    providers = abi_lookup._providers()

    assert providers == [
        (
            "etherscan",
            abi_lookup.etherscan.get_abi,
            "api-key",
            abi_lookup.etherscan.RATE_LIMIT_COOLDOWN,
        ),
        (
            "blockscout",
            abi_lookup.blockscout.get_abi,
            "blockscout-key",
            abi_lookup.blockscout.RATE_LIMIT_COOLDOWN,
        ),
    ]


def test_providers_quietly_supports_etherscan_token(monkeypatch) -> None:
    monkeypatch.setenv("ETHERSCAN_TOKEN", "token-key")

    providers = abi_lookup._providers()

    assert providers == [
        (
            "etherscan",
            abi_lookup.etherscan.get_abi,
            "token-key",
            abi_lookup.etherscan.RATE_LIMIT_COOLDOWN,
        )
    ]


def test_fetch_abi_without_providers_raises_key_configuration_error() -> None:
    with pytest.raises(ABINotFound, match="ETHERSCAN_API_KEY or BLOCKSCOUT_API_KEY"):
        abi_lookup._fetch_abi(1, ADDRESS)


def test_fetch_abi_returns_first_successful_provider(monkeypatch) -> None:
    calls = []

    def etherscan_get_abi(chain_id, address, api_key):
        calls.append(("etherscan", chain_id, address, api_key))
        raise ABINotFound("not verified")

    def blockscout_get_abi(chain_id, address, api_key):
        calls.append(("blockscout", chain_id, address, api_key))
        return ABI

    monkeypatch.setenv("ETHERSCAN_TOKEN", "etherscan-key")
    monkeypatch.setenv("BLOCKSCOUT_API_KEY", "blockscout-key")
    monkeypatch.setattr(abi_lookup.etherscan, "get_abi", etherscan_get_abi)
    monkeypatch.setattr(abi_lookup.blockscout, "get_abi", blockscout_get_abi)

    assert abi_lookup._fetch_abi(1, ADDRESS) == ABI
    assert calls == [
        ("etherscan", 1, ADDRESS, "etherscan-key"),
        ("blockscout", 1, ADDRESS, "blockscout-key"),
    ]


def test_fetch_abi_raises_last_rate_limit_when_all_providers_rate_limited(monkeypatch) -> None:
    def etherscan_get_abi(chain_id, address, api_key):
        raise ExplorerRateLimited("etherscan", 1)

    def blockscout_get_abi(chain_id, address, api_key):
        raise ExplorerRateLimited("blockscout", 2)

    monkeypatch.setenv("ETHERSCAN_API_KEY", "etherscan-key")
    monkeypatch.setenv("BLOCKSCOUT_API_KEY", "blockscout-key")
    monkeypatch.setattr(abi_lookup.etherscan, "get_abi", etherscan_get_abi)
    monkeypatch.setattr(abi_lookup.blockscout, "get_abi", blockscout_get_abi)
    monkeypatch.setattr(abi_lookup.time, "sleep", lambda seconds: None)

    with pytest.raises(ExplorerRateLimited) as excinfo:
        abi_lookup._fetch_abi(1, ADDRESS)

    assert excinfo.value.provider == "blockscout"


def test_fetch_abi_uses_provider_cooldown_when_rate_limit_has_no_retry_after(monkeypatch) -> None:
    monotonic_values = iter([100.0, 100.0, 100.0, 100.1, 100.2])
    sleeps = []

    def etherscan_get_abi(chain_id, address, api_key):
        raise ExplorerRateLimited("etherscan", None)

    monkeypatch.setenv("ETHERSCAN_API_KEY", "etherscan-key")
    monkeypatch.setattr(abi_lookup.etherscan, "get_abi", etherscan_get_abi)
    monkeypatch.setattr(abi_lookup.etherscan, "RATE_LIMIT_COOLDOWN", 0.5)
    monkeypatch.setattr(abi_lookup.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(abi_lookup.time, "sleep", sleeps.append)

    with pytest.raises(ExplorerRateLimited, match="etherscan"):
        abi_lookup._fetch_abi(1, ADDRESS)

    assert abi_lookup._rate_limited_until["etherscan"] == 100.5


def test_fetch_abi_raises_last_non_not_found_error(monkeypatch) -> None:
    def etherscan_get_abi(chain_id, address, api_key):
        raise ABINotFound("not verified")

    def blockscout_get_abi(chain_id, address, api_key):
        raise ExplorerError("bad response")

    monkeypatch.setenv("ETHERSCAN_API_KEY", "etherscan-key")
    monkeypatch.setenv("BLOCKSCOUT_API_KEY", "blockscout-key")
    monkeypatch.setattr(abi_lookup.etherscan, "get_abi", etherscan_get_abi)
    monkeypatch.setattr(abi_lookup.blockscout, "get_abi", blockscout_get_abi)

    with pytest.raises(ExplorerError, match="bad response"):
        abi_lookup._fetch_abi(1, ADDRESS)


def test_fetch_abi_raises_generic_not_found_when_all_providers_not_found(monkeypatch) -> None:
    monkeypatch.setenv("ETHERSCAN_API_KEY", "etherscan-key")
    monkeypatch.setattr(
        abi_lookup.etherscan,
        "get_abi",
        lambda chain_id, address, api_key: (_ for _ in ()).throw(ABINotFound("not verified")),
    )

    with pytest.raises(ABINotFound, match=f"ABI not found for {ADDRESS} on chain 1"):
        abi_lookup._fetch_abi(1, ADDRESS)


def test_partition_ready_splits_blocked_and_ready_providers(monkeypatch) -> None:
    providers = [
        ("etherscan", object(), "key-a", 1),
        ("blockscout", object(), "key-b", 1),
    ]
    monkeypatch.setattr(abi_lookup.time, "monotonic", lambda: 100.0)
    abi_lookup._rate_limited_until["etherscan"] = 99.0
    abi_lookup._rate_limited_until["blockscout"] = 101.0

    ready, blocked = abi_lookup._partition_ready(providers)

    assert ready == [providers[0]]
    assert blocked == [providers[1]]
    assert "etherscan" not in abi_lookup._rate_limited_until
    assert abi_lookup._rate_limited_until["blockscout"] == 101.0
