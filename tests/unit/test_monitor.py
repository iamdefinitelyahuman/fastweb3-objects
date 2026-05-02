from __future__ import annotations

from threading import Event

import pytest

from fw3_objects.monitor import TransactionMonitor, _chunks
from fw3_objects.transaction import TxStatus

TX_HASH = "0x" + "aa" * 32
SENDER = "0x" + "11" * 20


class DummyBatch:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class DummyEth:
    def __init__(self) -> None:
        self.transactions: dict[str, object] = {}
        self.receipts: dict[str, object] = {}
        self.nonces: dict[str, int] = {}
        self.tx_calls: list[str] = []
        self.receipt_calls: list[str] = []
        self.nonce_calls: list[tuple[str, str]] = []

    def get_transaction_by_hash(self, tx_hash: str):
        self.tx_calls.append(tx_hash)
        return self.transactions.get(tx_hash)

    def get_transaction_receipt(self, tx_hash: str):
        self.receipt_calls.append(tx_hash)
        return self.receipts.get(tx_hash)

    def get_transaction_count(self, sender: str, block: str):
        self.nonce_calls.append((sender, block))
        return self.nonces.get(sender, 0)


class DummyWeb3:
    def __init__(self) -> None:
        self.eth = DummyEth()

    def batch_requests(self) -> DummyBatch:
        return DummyBatch()


class DummyChain:
    def __init__(self) -> None:
        self.w3 = DummyWeb3()


class DummyTx:
    def __init__(
        self,
        tx_hash: str = TX_HASH,
        *,
        txdict: dict | None = None,
        status: TxStatus = TxStatus.UNSEEN,
        allow_unseen: bool = False,
    ) -> None:
        self.hash = tx_hash
        self._transaction = txdict or {}
        self._receipt = {}
        self._status = status
        self._allow_unseen = allow_unseen
        self._initialized = Event()
        self._finalized = Event()
        self.resolve_revert_reason_calls = 0

    def _resolve_revert_reason(self) -> None:
        self.resolve_revert_reason_calls += 1


@pytest.fixture
def monitor() -> TransactionMonitor:
    return TransactionMonitor(DummyChain())


def _txdict(nonce: int = 7) -> dict[str, object]:
    return {
        "hash": TX_HASH,
        "from": SENDER,
        "nonce": nonce,
        "value": 1,
    }


def test_chunks_yields_fixed_size_tuples() -> None:
    assert list(_chunks([1, 2, 3, 4, 5], 2)) == [(1, 2), (3, 4), (5,)]


def test_poll_once_returns_without_watched_transactions(monitor) -> None:
    monitor._poll_once()

    assert monitor.last_error is None


def test_poll_once_records_batch_errors_and_continues(monkeypatch, monitor) -> None:
    error = RuntimeError("boom")
    calls = []

    def fail(batch):
        calls.append(batch)
        raise error

    tx = DummyTx()
    monitor._watched.add(tx)
    monkeypatch.setattr(monitor, "_poll_batch", fail)

    monitor._poll_once()

    assert calls == [(tx,)]
    assert monitor.last_error is error


def test_poll_batch_marks_transaction_pending(monitor) -> None:
    tx = DummyTx()
    monitor.chain.w3.eth.transactions[TX_HASH] = _txdict()

    monitor._poll_batch((tx,))

    assert tx._transaction == _txdict()
    assert tx._status == TxStatus.PENDING
    assert tx._initialized.is_set()
    assert not tx._finalized.is_set()


def test_poll_batch_marks_confirmed_and_removes_from_watched(monitor) -> None:
    tx = DummyTx(txdict=_txdict(), status=TxStatus.PENDING)
    monitor._watched.add(tx)
    monitor.chain.w3.eth.transactions[TX_HASH] = _txdict()
    monitor.chain.w3.eth.receipts[TX_HASH] = {"status": 1, "blockNumber": 12}

    monitor._poll_batch((tx,))

    assert tx._transaction == _txdict()
    assert tx._receipt == {"status": 1, "blockNumber": 12}
    assert tx._status == TxStatus.CONFIRMED
    assert tx._finalized.is_set()
    assert tx not in monitor._watched


def test_poll_batch_marks_reverted_and_resolves_revert_reason(monitor) -> None:
    tx = DummyTx(txdict=_txdict(), status=TxStatus.PENDING)
    monitor.chain.w3.eth.receipts[TX_HASH] = {"status": 0, "blockNumber": 12}

    monitor._poll_batch((tx,))

    assert tx._status == TxStatus.REVERTED
    assert tx._finalized.is_set()
    assert tx.resolve_revert_reason_calls == 1


def test_poll_batch_marks_seen_transaction_dropped_then_pending_again(monitor) -> None:
    tx = DummyTx(txdict=_txdict(), status=TxStatus.PENDING)
    monitor.chain.w3.eth.nonces[SENDER] = 7

    monitor._poll_batch((tx,))

    assert tx._status == TxStatus.DROPPED
    assert not tx._finalized.is_set()

    monitor.chain.w3.eth.transactions[TX_HASH] = _txdict()
    monitor._poll_batch((tx,))

    assert tx._status == TxStatus.PENDING
    assert not tx._finalized.is_set()


def test_poll_batch_marks_transaction_replaced_when_sender_nonce_advanced(monitor) -> None:
    tx = DummyTx(txdict=_txdict(nonce=7), status=TxStatus.PENDING)
    monitor.chain.w3.eth.nonces[SENDER] = 8

    monitor._poll_batch((tx,))

    assert tx._status == TxStatus.REPLACED
    assert tx._finalized.is_set()


def test_poll_batch_finalizes_never_seen_transaction_when_unseen_not_allowed(monitor) -> None:
    tx = DummyTx()

    monitor._poll_batch((tx,))

    assert tx._status == TxStatus.UNSEEN
    assert tx._initialized.is_set()
    assert tx._finalized.is_set()


def test_poll_batch_keeps_allowed_unseen_transaction_watched(monitor) -> None:
    tx = DummyTx(allow_unseen=True)
    monitor._watched.add(tx)

    monitor._poll_batch((tx,))

    assert tx._status == TxStatus.UNSEEN
    assert tx._initialized.is_set()
    assert not tx._finalized.is_set()
    assert tx in monitor._watched


def test_poll_batch_allows_unseen_transaction_to_confirm_from_receipt_only(monitor) -> None:
    tx = DummyTx(allow_unseen=True)
    monitor.chain.w3.eth.receipts[TX_HASH] = {"status": 1, "blockNumber": 12}

    monitor._poll_batch((tx,))

    assert tx._transaction == {}
    assert tx._receipt == {"status": 1, "blockNumber": 12}
    assert tx._status == TxStatus.CONFIRMED
    assert tx._finalized.is_set()


def test_poll_batch_receipt_wins_over_missing_transaction_data(monitor) -> None:
    tx = DummyTx(txdict=_txdict(), status=TxStatus.PENDING)
    monitor.chain.w3.eth.receipts[TX_HASH] = {"status": 1, "blockNumber": 12}

    monitor._poll_batch((tx,))

    assert tx._transaction == _txdict()
    assert tx._receipt == {"status": 1, "blockNumber": 12}
    assert tx._status == TxStatus.CONFIRMED
    assert tx._finalized.is_set()


def test_poll_batch_queries_latest_nonce_once_per_known_sender(monitor) -> None:
    tx_a = DummyTx(TX_HASH, txdict=_txdict(nonce=7), status=TxStatus.PENDING)
    tx_b = DummyTx("0x" + "bb" * 32, txdict=_txdict(nonce=8), status=TxStatus.PENDING)
    monitor.chain.w3.eth.nonces[SENDER] = 7

    monitor._poll_batch((tx_a, tx_b))

    assert monitor.chain.w3.eth.nonce_calls == [(SENDER, "latest")]
    assert tx_a._status == TxStatus.DROPPED
    assert tx_b._status == TxStatus.DROPPED


def test_poll_batch_does_not_query_nonce_for_unknown_sender(monitor) -> None:
    tx = DummyTx(status=TxStatus.PENDING)

    monitor._poll_batch((tx,))

    assert monitor.chain.w3.eth.nonce_calls == []
    assert tx._status == TxStatus.DROPPED
    assert not tx._finalized.is_set()


def test_poll_once_splits_large_watch_set(monkeypatch, monitor) -> None:
    batches: list[tuple[DummyTx, ...]] = []
    txs = [DummyTx("0x" + f"{idx:064x}") for idx in range(35)]
    for tx in txs:
        monitor._watched.add(tx)

    monkeypatch.setattr("fw3_objects.monitor.MAX_BATCH_SIZE", 30)
    monkeypatch.setattr(monitor, "_poll_batch", lambda batch: batches.append(batch))

    monitor._poll_once()

    assert [len(batch) for batch in batches] == [10, 10, 10, 5]
