from __future__ import annotations

import itertools
import os
import queue
import threading
import time
from dataclasses import dataclass, field

from fw3_objects.errors import ABINotFound, ExplorerError, ExplorerRateLimited

from . import blockscout, etherscan

LOW_PRIORITY = 10
HIGH_PRIORITY = 0
NEGATIVE_ABI_TTL = 30


@dataclass
class ABILookupJob:
    chain_id: int
    address: str
    ignore_negative_cache: bool = False
    priority: int = LOW_PRIORITY
    _event: threading.Event = field(default_factory=threading.Event, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _abi: list[dict] | None = field(default=None, init=False)
    _error: BaseException | None = field(default=None, init=False)
    _callbacks: list = field(default_factory=list, init=False)

    def add_done_callback(self, callback) -> None:
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)
                return
            abi = self._abi

        if abi is not None:
            callback(abi)

    def bump_priority(self, priority: int) -> None:
        with self._lock:
            if self._event.is_set() or priority >= self.priority:
                return
            self.priority = priority
        _enqueue(self)

    def wait(self) -> list[dict]:
        self._event.wait()
        if self._error is not None:
            raise self._error
        return self._abi

    def set_result(self, abi: list[dict]) -> None:
        with self._lock:
            self._abi = abi
            callbacks = tuple(self._callbacks)
            self._callbacks.clear()
            self._event.set()

        for callback in callbacks:
            try:
                callback(abi)
            except Exception:
                pass

    def set_error(self, error: BaseException) -> None:
        with self._lock:
            self._error = error
            self._callbacks.clear()
            self._event.set()

    @property
    def done(self) -> bool:
        return self._event.is_set()


_sequence = itertools.count()
_queue: queue.PriorityQueue[tuple[int, int, ABILookupJob]] = queue.PriorityQueue()
_pending: dict[tuple[int, str], ABILookupJob] = {}
_negative_cache: dict[tuple[int, str], float] = {}
_rate_limited_until: dict[str, float] = {}
_state_lock = threading.RLock()
_worker_started = False


def fetch_abi(
    chain_id: int,
    address: str,
    *,
    priority: int = LOW_PRIORITY,
    ignore_negative_cache: bool = False,
    on_success=None,
) -> ABILookupJob:
    chain_id = int(chain_id)
    address = address.lower()
    key = (chain_id, address)

    with _state_lock:
        existing = _pending.get(key)
        if existing is not None and not existing.done and not ignore_negative_cache:
            if on_success is not None:
                existing.add_done_callback(on_success)
            existing.bump_priority(priority)
            return existing

        if not ignore_negative_cache:
            expires_at = _negative_cache.get(key)
            if expires_at is not None and expires_at > time.monotonic():
                job = ABILookupJob(
                    chain_id,
                    address,
                    ignore_negative_cache=ignore_negative_cache,
                    priority=priority,
                )
                job.set_error(ABINotFound(f"ABI not found for {address} on chain {chain_id}"))
                return job
            if expires_at is not None:
                _negative_cache.pop(key, None)

        job = ABILookupJob(
            chain_id, address, ignore_negative_cache=ignore_negative_cache, priority=priority
        )
        if on_success is not None:
            job.add_done_callback(on_success)
        _pending[key] = job
        _ensure_worker_started()

    _enqueue(job)
    return job


def _ensure_worker_started() -> None:
    global _worker_started

    if _worker_started:
        return

    thread = threading.Thread(target=_worker, name="fw3-objects-abi-lookup", daemon=True)
    thread.start()
    _worker_started = True


def _enqueue(job: ABILookupJob) -> None:
    _queue.put((job.priority, next(_sequence), job))


def _worker() -> None:
    while True:
        priority, _, job = _queue.get()
        try:
            if job.done or priority != job.priority:
                continue
            _run_job(job)
        finally:
            _queue.task_done()


def _run_job(job: ABILookupJob) -> None:
    key = (job.chain_id, job.address)

    try:
        abi = _fetch_abi(job.chain_id, job.address)
    except BaseException as exc:
        job.set_error(exc)
        with _state_lock:
            should_cache = _pending.get(key) is job
            if should_cache and not job.ignore_negative_cache and isinstance(exc, ABINotFound):
                _negative_cache[key] = time.monotonic() + NEGATIVE_ABI_TTL
    else:
        job.set_result(abi)
    finally:
        with _state_lock:
            if _pending.get(key) is job:
                _pending.pop(key, None)


def _fetch_abi(chain_id: int, address: str) -> list[dict]:
    providers = _providers()
    if not providers:
        raise ABINotFound(
            "No block explorer API keys configured, set ETHERSCAN_API_KEY or BLOCKSCOUT_API_KEY"
        )

    errors = []

    while providers:
        ready, blocked = _partition_ready(providers)

        for name, func, api_key, cooldown in ready:
            try:
                return func(chain_id, address, api_key)
            except ExplorerRateLimited as exc:
                retry_after = exc.retry_after if exc.retry_after is not None else cooldown
                with _state_lock:
                    _rate_limited_until[name] = time.monotonic() + retry_after
                errors.append(exc)
            except (ABINotFound, ExplorerError) as exc:
                errors.append(exc)

        providers = blocked
        if not providers:
            break

        sleep_for = (
            min(_rate_limited_until.get(name, 0) for name, *_ in providers) - time.monotonic()
        )
        if sleep_for > 0:
            time.sleep(sleep_for)

    if errors:
        if all(isinstance(error, ExplorerRateLimited) for error in errors):
            raise errors[-1]

        non_not_found_errors = [error for error in errors if not isinstance(error, ABINotFound)]
        if non_not_found_errors:
            raise non_not_found_errors[-1]

    raise ABINotFound(f"ABI not found for {address} on chain {chain_id}")


def _providers():
    providers = []

    etherscan_key = os.getenv("ETHERSCAN_API_KEY")
    if etherscan_key:
        providers.append(
            ("etherscan", etherscan.get_abi, etherscan_key, etherscan.RATE_LIMIT_COOLDOWN)
        )

    blockscout_key = os.getenv("BLOCKSCOUT_API_KEY")
    if blockscout_key:
        providers.append(
            ("blockscout", blockscout.get_abi, blockscout_key, blockscout.RATE_LIMIT_COOLDOWN)
        )

    return providers


def _partition_ready(providers):
    now = time.monotonic()
    ready = []
    blocked = []

    with _state_lock:
        for provider in providers:
            name = provider[0]
            expires_at = _rate_limited_until.get(name)
            if expires_at is None or expires_at <= now:
                _rate_limited_until.pop(name, None)
                ready.append(provider)
            else:
                blocked.append(provider)

    return ready, blocked
