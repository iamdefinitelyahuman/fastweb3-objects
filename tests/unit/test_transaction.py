from __future__ import annotations

import pytest

from fw3_objects import abi
from fw3_objects.chain import Chain
from fw3_objects.errors import NoActiveChain, TransactionNotFound
from fw3_objects.transaction import Transaction, TxStatus, _bump_fee, _decode_revert_reason

TX_HASH = "0x" + "aa" * 32
SENDER = "0x" + "11" * 20
RECEIVER = "0x" + "22" * 20


class DummyMonitor:
    def __init__(self) -> None:
        self.watched = []

    def watch(self, tx) -> None:
        self.watched.append(tx)


class DummyEth:
    def block_number(self) -> int:
        return 12


class DummyWeb3:
    def __init__(self) -> None:
        self.eth = DummyEth()


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
    chain._Chain__transaction_monitor = DummyMonitor()
    return chain


def _txdict() -> dict[str, object]:
    return {
        "hash": TX_HASH,
        "from": SENDER,
        "to": RECEIVER,
        "value": 123,
        "nonce": 7,
        "gas": 21_000,
        "gasPrice": 4,
        "maxFeePerGas": 5,
        "maxPriorityFeePerGas": 2,
        "input": "0x1234",
        "type": 2,
        "blockHash": "0x" + "33" * 32,
        "blockNumber": 10,
        "transactionIndex": 1,
    }


def test_transaction_validates_hash_type_and_shape(chain) -> None:
    with pytest.raises(TypeError, match="Transaction hash must be a string"):
        Transaction(b"\xaa" * 32, chain=chain)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Invalid transaction hash"):
        Transaction("0xabc", chain=chain)

    with pytest.raises(ValueError, match="Invalid transaction hash"):
        Transaction("0x" + "gg" * 32, chain=chain)


def test_transaction_requires_chain_or_default_chain() -> None:
    with pytest.raises(NoActiveChain, match="No chain specified for Transaction"):
        Transaction(TX_HASH)


def test_transaction_uses_default_chain(chain) -> None:
    with chain.as_default():
        tx = Transaction(TX_HASH)

    assert tx.chain is chain


def test_transaction_seeded_txdict_is_initialized_and_watched(chain) -> None:
    tx = Transaction(TX_HASH.upper().replace("X", "x"), chain=chain, _txdict=_txdict())

    assert tx.hash == TX_HASH
    assert tx._initialized.is_set()
    assert chain._transaction_monitor.watched == [tx]


def test_transaction_properties_read_transaction_and_receipt_data(chain) -> None:
    tx = Transaction(TX_HASH, chain=chain, _txdict=_txdict())
    tx._receipt = {
        "status": 1,
        "blockHash": "0x" + "44" * 32,
        "blockNumber": 11,
        "transactionIndex": 2,
        "gasUsed": 20_000,
        "cumulativeGasUsed": 30_000,
        "effectiveGasPrice": 6,
        "contractAddress": RECEIVER,
        "logs": [{"data": "0x"}],
        "logsBloom": "0x" + "00" * 256,
    }

    assert tx.sender.address == SENDER
    assert tx.receiver.address == RECEIVER
    assert tx.value == 123
    assert tx.nonce == 7
    assert tx.gas == 21_000
    assert tx.gas_price == 4
    assert tx.max_fee_per_gas == 5
    assert tx.max_priority_fee_per_gas == 2
    assert tx.input == "0x1234"
    assert tx.type == 2
    assert tx.block_hash == "0x" + "44" * 32
    assert tx.block_number == 11
    assert tx.transaction_index == 2
    assert tx.status == TxStatus.CONFIRMED
    assert tx.gas_used == 20_000
    assert tx.cumulative_gas_used == 30_000
    assert tx.effective_gas_price == 6
    assert tx.contract_address == RECEIVER
    assert tx.logs == [{"data": "0x"}]
    assert tx.logs_bloom == "0x" + "00" * 256


def test_unseen_transaction_without_allow_unseen_raises_after_initial_update(chain) -> None:
    tx = Transaction(TX_HASH, chain=chain)
    tx._initialized.set()

    with pytest.raises(TransactionNotFound, match=TX_HASH):
        tx.value


def test_allow_unseen_transaction_returns_empty_properties_after_initial_update(chain) -> None:
    tx = Transaction(TX_HASH, chain=chain, allow_unseen=True)
    tx._initialized.set()

    assert tx.value is None
    assert tx.status == TxStatus.UNSEEN


def test_confirmations_uses_chain_height(chain) -> None:
    tx = Transaction(TX_HASH, chain=chain, _txdict=_txdict())
    tx._receipt = {"blockNumber": 10}

    assert tx.confirmations() == 3


def test_confirmations_returns_zero_before_block_number(chain) -> None:
    tx = Transaction(TX_HASH, chain=chain, _txdict={"hash": TX_HASH})

    assert tx.confirmations() == 0


def test_wait_returns_immediately_for_zero_confirmations(chain) -> None:
    tx = Transaction(TX_HASH, chain=chain)

    assert tx.wait(required_confs=0) is None


def test_events_are_lazy_and_cached(monkeypatch, chain) -> None:
    calls = []

    class FakeEventList:
        def __init__(self, logs, *, chain=None):
            self.logs = tuple(logs)
            self.chain = chain
            calls.append((self.logs, chain))

    monkeypatch.setattr("fw3_objects.transaction.EventList", FakeEventList)

    tx = Transaction(TX_HASH, chain=chain, _txdict=_txdict())
    tx._receipt = {"logs": [{"data": "0x"}]}

    events = tx.events

    assert events is tx.events
    assert events.logs == ({"data": "0x"},)
    assert events.chain is chain
    assert calls == [(({"data": "0x"},), chain)]


def test_events_use_empty_logs_when_receipt_has_no_logs(monkeypatch, chain) -> None:
    calls = []

    class FakeEventList:
        def __init__(self, logs, *, chain=None):
            calls.append((tuple(logs), chain))

    monkeypatch.setattr("fw3_objects.transaction.EventList", FakeEventList)

    tx = Transaction(TX_HASH, chain=chain, _txdict=_txdict())

    tx.events

    assert calls == [((), chain)]


def test_bump_fee_uses_increment_but_always_increases_by_at_least_one() -> None:
    assert _bump_fee(100, 1.125) == 112
    assert _bump_fee(1, 1.001) == 2


def test_decode_revert_reason_handles_error_string() -> None:
    data = "0x08c379a0" + abi.encode("(string)", ("nope",)).hex()

    assert _decode_revert_reason(data) == "nope"


def test_decode_revert_reason_handles_known_panic_code() -> None:
    data = "0x4e487b71" + abi.encode("(uint256)", (0x11,)).hex()

    assert _decode_revert_reason(data) == "Panic(0x11): arithmetic underflow or overflow"


def test_decode_revert_reason_handles_unknown_panic_code() -> None:
    data = "0x4e487b71" + abi.encode("(uint256)", (0x99,)).hex()

    assert _decode_revert_reason(data) == "Panic(0x99)"


def test_decode_revert_reason_ignores_unknown_or_non_string_data() -> None:
    assert _decode_revert_reason("0x12345678") is None
    assert _decode_revert_reason(None) is None


def test_wait_blocks_until_required_confirmations(monkeypatch, chain) -> None:
    tx = Transaction(TX_HASH, chain=chain, _txdict=_txdict())
    tx._receipt = {"blockNumber": 10, "status": 1}
    tx._finalized.set()
    heights = iter([10, 11])
    sleeps: list[int] = []

    monkeypatch.setattr(chain, "height", lambda: next(heights))
    monkeypatch.setattr(
        "fw3_objects.transaction.time.sleep", lambda seconds: sleeps.append(seconds)
    )

    tx.wait(required_confs=2)

    assert sleeps == [1]


def test_wait_returns_after_finalization_for_one_confirmation(chain) -> None:
    tx = Transaction(TX_HASH, chain=chain, _txdict=_txdict())
    tx._receipt = {"blockNumber": 10, "status": 1}
    tx._finalized.set()

    assert tx.wait() is None


def test_replace_rejects_finalized_transaction(chain) -> None:
    tx = Transaction(TX_HASH, chain=chain, _txdict=_txdict())
    tx._receipt = {"status": 1}
    tx._finalized.set()

    with pytest.raises(ValueError, match="Cannot replace transaction with status CONFIRMED"):
        tx.replace()


def test_replace_bumps_legacy_gas_price_and_rebroadcasts(monkeypatch, chain) -> None:
    calls: list[dict[str, object]] = []

    class Signer:
        has_private_key = True

        def transact(self, **kwargs):
            calls.append(kwargs)
            return "replacement"

    tx = Transaction(TX_HASH, chain=chain, _txdict=_txdict())
    monkeypatch.setattr("fw3_objects.transaction.Transaction.sender", Signer())

    assert tx.replace(increment=1.5) == "replacement"
    assert len(calls) == 1
    assert calls[0].pop("to").address == RECEIVER
    assert calls == [
        {
            "value": 123,
            "data": "0x1234",
            "gas_limit": 21_000,
            "nonce": 7,
            "chain": chain,
            "gas_price": 6,
        }
    ]


def test_replace_bumps_eip1559_fees_and_rebroadcasts(monkeypatch, chain) -> None:
    calls: list[dict[str, object]] = []
    txdict = _txdict()
    txdict["gasPrice"] = None

    class Signer:
        has_private_key = True

        def transact(self, **kwargs):
            calls.append(kwargs)
            return "replacement"

    tx = Transaction(TX_HASH, chain=chain, _txdict=txdict)
    monkeypatch.setattr("fw3_objects.transaction.Transaction.sender", Signer())

    assert tx.replace(increment=1.5) == "replacement"
    assert len(calls) == 1
    assert calls[0].pop("to").address == RECEIVER
    assert calls == [
        {
            "value": 123,
            "data": "0x1234",
            "gas_limit": 21_000,
            "nonce": 7,
            "chain": chain,
            "max_fee_per_gas": 7,
            "max_priority_fee_per_gas": 3,
        }
    ]


def test_resolve_revert_reason_sets_data_and_reason(monkeypatch, chain) -> None:
    data = "0x08c379a0" + abi.encode("(string)", ("execution reverted",)).hex()
    call_kwargs: list[dict[str, object]] = []

    class Details:
        def __init__(self, data: str) -> None:
            self.data = data

    class FakeRPCError(Exception):
        def __init__(self, data: str) -> None:
            self.details = Details(data)

    def call(**kwargs):
        call_kwargs.append(kwargs)
        raise FakeRPCError(data)

    monkeypatch.setattr("fw3_objects.transaction.RPCError", FakeRPCError)
    monkeypatch.setattr(chain.w3.eth, "call", call, raising=False)

    tx = Transaction(TX_HASH, chain=chain, _txdict=_txdict())
    tx._receipt = {"blockNumber": 12, "status": 0}

    tx._resolve_revert_reason()

    assert tx._revert_ready.wait(1)
    assert tx.revert_data.data == data
    assert tx.revert_reason == "execution reverted"
    assert call_kwargs == [
        {
            "from_": SENDER,
            "to": RECEIVER,
            "value": 123,
            "data": "0x1234",
            "gas": 21_000,
            "block": 11,
        }
    ]
