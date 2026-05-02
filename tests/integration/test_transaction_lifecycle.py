from __future__ import annotations

from threading import RLock

import pytest

from fw3_objects.account import Account
from fw3_objects.chain import Chain
from fw3_objects.errors import TransactionNotFound
from fw3_objects.monitor import TransactionMonitor
from fw3_objects.transaction import Transaction, TxStatus

TX_HASH = "0x" + "aa" * 32
SENDER = "0x" + "11" * 20
RECEIVER = "0x" + "22" * 20


class DummyBatch:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class DummyEth:
    def __init__(self) -> None:
        self.transactions: dict[str, object] = {}
        self.receipts: dict[str, object] = {}
        self.nonces: dict[tuple[str, str], int] = {}
        self.sent_raw_transactions: list[bytes] = []
        self.estimated_gas_calls: list[dict[str, object]] = []

    def get_transaction_count(self, address: str, block: str = "latest") -> int:
        return self.nonces.get((address, block), 0)

    def estimate_gas(self, **kwargs) -> int:
        self.estimated_gas_calls.append(kwargs)
        return 21_000

    def max_priority_fee_per_gas(self) -> int:
        return 2_000_000_000

    def fee_history(self, block_count: int, newest_block: str, reward_percentiles: list) -> dict:
        return {"baseFeePerGas": [1_000_000_000]}

    def send_raw_transaction(self, raw_tx: bytes) -> str:
        self.sent_raw_transactions.append(raw_tx)
        return TX_HASH

    def get_transaction_by_hash(self, tx_hash: str):
        return self.transactions.get(tx_hash)

    def get_transaction_receipt(self, tx_hash: str):
        return self.receipts.get(tx_hash)


class DummyWeb3:
    def __init__(self) -> None:
        self.eth = DummyEth()

    def batch_requests(self) -> DummyBatch:
        return DummyBatch()


class ManualTransactionMonitor(TransactionMonitor):
    def __init__(self, chain) -> None:
        super().__init__(chain)
        self._lock = RLock()

    def watch(self, tx) -> None:
        with self._lock:
            self._watched.add(tx)


class Signed:
    raw_transaction = b"\xde\xad"


@pytest.fixture(autouse=True)
def reset_chain_state() -> None:
    Chain._instances.clear()
    Chain._set_default_chain(None, False)
    yield
    Chain._instances.clear()
    Chain._set_default_chain(None, False)


@pytest.fixture
def chain() -> Chain:
    chain = Chain(1)
    chain._w3 = DummyWeb3()
    chain._Chain__transaction_monitor = ManualTransactionMonitor(chain)
    return chain


@pytest.fixture
def monitor(chain) -> ManualTransactionMonitor:
    return chain._transaction_monitor


def _pending_txdict(nonce: int = 7) -> dict[str, object]:
    return {
        "hash": TX_HASH,
        "from": SENDER,
        "to": RECEIVER,
        "value": 1,
        "nonce": nonce,
        "gas": 21_000,
        "input": "0x",
        "type": 2,
        "maxFeePerGas": 2_000_000_000,
        "maxPriorityFeePerGas": 2_000_000_000,
    }


def _receipt(status: int = 1) -> dict[str, object]:
    return {
        "transactionHash": TX_HASH,
        "status": status,
        "blockNumber": 12,
        "blockHash": "0x" + "33" * 32,
        "transactionIndex": 0,
        "gasUsed": 20_000,
        "cumulativeGasUsed": 20_000,
        "effectiveGasPrice": 2_000_000_000,
        "logs": [],
        "logsBloom": "0x" + "00" * 256,
        "contractAddress": None,
    }


def test_broadcasted_transaction_is_seeded_watched_and_confirmed(
    monkeypatch,
    chain,
    monitor,
) -> None:
    account = Account(SENDER, chain=chain)
    monkeypatch.setattr(account, "sign_transaction", lambda tx: Signed())
    chain.w3.eth.nonces[(account.address, "latest")] = 7

    tx = account.transact(to=RECEIVER, value=1)

    assert tx.hash == TX_HASH
    assert tx.sender.address == account.address
    assert tx.receiver.address == RECEIVER
    assert tx.nonce == 7
    assert tx.gas == 21_000
    assert tx.value == 1
    assert chain.w3.eth.sent_raw_transactions == [b"\xde\xad"]
    assert tx in monitor._watched

    chain.w3.eth.transactions[TX_HASH] = _pending_txdict()
    monitor._poll_once()

    assert tx.status == TxStatus.PENDING
    assert not tx._finalized.is_set()

    chain.w3.eth.receipts[TX_HASH] = _receipt(status=1)
    monitor._poll_once()

    assert tx.status == TxStatus.CONFIRMED
    assert tx.block_number == 12
    assert tx.gas_used == 20_000
    assert tx._finalized.is_set()
    assert tx not in monitor._watched


def test_pending_transaction_can_drop_reappear_and_confirm(chain, monitor) -> None:
    tx = Transaction(TX_HASH, chain=chain, allow_unseen=True, _txdict=_pending_txdict())
    chain.w3.eth.transactions[TX_HASH] = _pending_txdict()

    monitor._poll_once()
    assert tx.status == TxStatus.PENDING

    chain.w3.eth.transactions.clear()
    chain.w3.eth.nonces[(SENDER, "latest")] = 7
    monitor._poll_once()
    assert tx.status == TxStatus.DROPPED
    assert not tx._finalized.is_set()

    chain.w3.eth.transactions[TX_HASH] = _pending_txdict()
    monitor._poll_once()
    assert tx.status == TxStatus.PENDING

    chain.w3.eth.receipts[TX_HASH] = _receipt(status=1)
    monitor._poll_once()
    assert tx.status == TxStatus.CONFIRMED
    assert tx._finalized.is_set()


def test_pending_transaction_is_replaced_when_sender_nonce_advances(chain, monitor) -> None:
    tx = Transaction(TX_HASH, chain=chain, allow_unseen=True, _txdict=_pending_txdict(nonce=7))
    chain.w3.eth.nonces[(SENDER, "latest")] = 8

    monitor._poll_once()

    assert tx.status == TxStatus.REPLACED
    assert tx._finalized.is_set()
    assert tx not in monitor._watched


def test_unseen_allowed_transaction_can_confirm_from_receipt(chain, monitor) -> None:
    tx = Transaction(TX_HASH, chain=chain, allow_unseen=True)

    monitor._poll_once()
    assert tx.status == TxStatus.UNSEEN
    assert not tx._finalized.is_set()

    chain.w3.eth.receipts[TX_HASH] = _receipt(status=1)
    monitor._poll_once()

    assert tx.status == TxStatus.CONFIRMED
    assert tx.block_number == 12
    assert tx._finalized.is_set()


def test_never_seen_transaction_finalizes_as_not_found(chain, monitor) -> None:
    tx = Transaction(TX_HASH, chain=chain)

    monitor._poll_once()

    assert tx._initialized.is_set()
    assert tx._finalized.is_set()
    assert tx._status == TxStatus.UNSEEN
    assert tx not in monitor._watched
    with pytest.raises(TransactionNotFound, match=TX_HASH):
        tx.status


def test_broadcasted_transaction_can_revert(monkeypatch, chain, monitor) -> None:
    account = Account(SENDER, chain=chain)
    monkeypatch.setattr(account, "sign_transaction", lambda tx: Signed())
    chain.w3.eth.nonces[(account.address, "latest")] = 7
    resolve_calls: list[str] = []

    def resolve_revert_reason(tx):
        resolve_calls.append(tx.hash)
        tx._revert_data = "0x"
        tx._revert_reason = None
        tx._revert_ready.set()

    monkeypatch.setattr(Transaction, "_resolve_revert_reason", resolve_revert_reason)

    tx = account.transact(to=RECEIVER, value=1)
    chain.w3.eth.transactions[TX_HASH] = _pending_txdict()
    monitor._poll_once()
    assert tx.status == TxStatus.PENDING

    chain.w3.eth.receipts[TX_HASH] = _receipt(status=0)
    monitor._poll_once()

    assert tx.status == TxStatus.REVERTED
    assert tx.revert_data == "0x"
    assert tx.revert_reason is None
    assert resolve_calls == [TX_HASH]
    assert tx._finalized.is_set()
    assert tx not in monitor._watched


def test_unseen_not_allowed_transaction_can_confirm_if_receipt_appears_first(
    chain,
    monitor,
) -> None:
    tx = Transaction(TX_HASH, chain=chain)
    chain.w3.eth.receipts[TX_HASH] = _receipt(status=1)

    monitor._poll_once()

    assert tx.status == TxStatus.CONFIRMED
    assert tx.block_number == 12
    assert tx._finalized.is_set()
    assert tx not in monitor._watched


def test_pending_transaction_can_drop_without_replacement_then_wait_after_confirm(
    chain,
    monitor,
) -> None:
    tx = Transaction(TX_HASH, chain=chain, allow_unseen=True, _txdict=_pending_txdict())
    chain.w3.eth.transactions[TX_HASH] = _pending_txdict()

    monitor._poll_once()
    assert tx.status == TxStatus.PENDING

    chain.w3.eth.transactions.clear()
    chain.w3.eth.nonces[(SENDER, "latest")] = 7
    monitor._poll_once()
    assert tx.status == TxStatus.DROPPED

    chain.w3.eth.receipts[TX_HASH] = _receipt(status=1)
    monitor._poll_once()

    assert tx.status == TxStatus.CONFIRMED
    assert tx.wait() is None
