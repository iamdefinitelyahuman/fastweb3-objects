from __future__ import annotations

import threading

import pytest

from fw3_objects.chain import Chain, configure_chain
from fw3_objects.errors import ChainMismatch


class DummyEth:
    def __init__(self) -> None:
        self._block_number = 0
        self.block_by_number_calls: list[object] = []
        self.block_by_hash_calls: list[object] = []
        self.tx_calls: list[object] = []
        self.fee_history_calls: list[tuple[object, object, object]] = []
        self.priority_fee_calls = 0

    def block_number(self) -> int:
        return self._block_number

    def get_block_by_number(self, value):
        self.block_by_number_calls.append(value)

        if isinstance(value, int):
            number = value
        elif isinstance(value, str) and value.startswith("0x"):
            number = int(value, 16)
        else:
            number = value

        gas_limit = number * 1000 if isinstance(number, int) else 123
        return {"number": number, "gasLimit": gas_limit}

    def get_block_by_hash(self, value):
        self.block_by_hash_calls.append(value)
        return {"hash": value, "gasLimit": 999}

    def get_transaction_by_hash(self, value):
        self.tx_calls.append(value)
        return {"hash": value}

    def fee_history(self, block_count, newest_block, reward_percentiles):
        self.fee_history_calls.append((block_count, newest_block, reward_percentiles))
        return {"baseFeePerGas": [12345]}

    def max_priority_fee_per_gas(self) -> int:
        self.priority_fee_calls += 1
        return 77


class DummyWeb3:
    def __init__(self, chain_id: int, **kwargs) -> None:
        self.chain_id = chain_id
        self.kwargs = kwargs
        self.eth = DummyEth()


@pytest.fixture(autouse=True)
def reset_chain_state() -> None:
    Chain._instances.clear()
    Chain._set_default_chain(None, False)
    yield
    Chain._instances.clear()
    Chain._set_default_chain(None, False)


@pytest.fixture
def chain_module(monkeypatch):
    from fw3_objects import chain as chain_module

    monkeypatch.setattr(chain_module, "Web3", DummyWeb3)
    return chain_module


def _default_chain() -> Chain | None:
    return Chain._get_default_chain()[0]


def test_chain_is_canonical_per_chain_id(chain_module) -> None:
    chain_a = Chain(1)
    chain_b = Chain(1)
    chain_c = Chain(2)

    assert chain_a is chain_b
    assert chain_a is not chain_c


def test_init_creates_default_web3_once_per_canonical_instance(monkeypatch, chain_module) -> None:
    calls: list[tuple[int, dict[str, object]]] = []

    def fake_create_w3(self, **w3_params):
        calls.append((self.id, dict(w3_params)))
        self._w3_params = dict(w3_params)
        self._w3 = DummyWeb3(chain_id=self.id, **w3_params)

    monkeypatch.setattr(Chain, "_create_w3", fake_create_w3)

    chain_a = Chain(1)
    chain_b = Chain(1)

    assert chain_a is chain_b
    assert calls == []

    assert chain_a.w3 is not None
    assert calls == [(1, {})]


def test_repr_int_id_and_w3_property(chain_module) -> None:
    chain = Chain(1)

    assert repr(chain) == "Chain(1)"
    assert int(chain) == 1
    assert chain.id == 1
    assert isinstance(chain.w3, DummyWeb3)
    assert chain.w3.chain_id == 1


def test_configure_replaces_web3_and_stores_params(chain_module) -> None:
    chain = Chain(1)
    original_w3 = chain.w3

    chain._create_w3(primary_endpoint="https://rpc.example", provider="custom")

    assert chain.w3 is not original_w3
    assert chain._w3_params == {
        "primary_endpoint": "https://rpc.example",
        "provider": "custom",
    }
    assert chain.w3.kwargs == chain._w3_params


def test_configure_chain_uses_canonical_chain_instance(chain_module) -> None:
    chain = Chain(1)
    original_w3 = chain.w3

    configure_chain(1, primary_endpoint="https://rpc.example")

    assert Chain(1) is chain
    assert chain.w3 is not original_w3
    assert chain.w3.kwargs == {"primary_endpoint": "https://rpc.example"}


def test_configure_chain_accepts_chain_instance(chain_module) -> None:
    chain = Chain(1)
    original_w3 = chain.w3

    configure_chain(chain, provider="custom")

    assert chain.w3 is not original_w3
    assert chain.w3.kwargs == {"provider": "custom"}


def test_height_and_len_delegate_to_web3(chain_module) -> None:
    chain = Chain(1)
    chain.w3.eth._block_number = 12

    assert chain.height() == 12
    assert len(chain) == 13


def test_getitem_with_positive_index_reads_that_block(chain_module) -> None:
    chain = Chain(1)

    block = chain[7]

    assert block["number"] == 7
    assert chain.w3.eth.block_by_number_calls == ["0x7"]


def test_getitem_with_negative_index_is_relative_to_tip(chain_module) -> None:
    chain = Chain(1)
    chain.w3.eth._block_number = 12

    latest = chain[-1]
    third_from_tip = chain[-3]

    assert latest["number"] == "latest"
    assert third_from_tip["number"] == 10
    assert chain.w3.eth.block_by_number_calls == ["latest", "0xa"]


def test_getitem_rejects_slice(chain_module) -> None:
    chain = Chain(1)

    with pytest.raises(TypeError, match="Slicing is not supported"):
        chain[1:5]


def test_getitem_rejects_non_int_index(chain_module) -> None:
    chain = Chain(1)

    with pytest.raises(TypeError, match="block_number must be int"):
        chain["1"]  # type: ignore[index]


def test_getitem_negative_index_out_of_range_raises_index_error(chain_module) -> None:
    chain = Chain(1)
    chain.w3.eth._block_number = 2

    block = chain[-4]
    with pytest.raises(IndexError, match="block index out of range"):
        block["number"]


def test_block_gas_limit_uses_latest_block(chain_module) -> None:
    chain = Chain(1)

    assert int(chain.block_gas_limit()) == 123
    assert chain.w3.eth.block_by_number_calls == ["latest"]


def test_base_fee_uses_fee_history(chain_module) -> None:
    chain = Chain(1)

    assert chain.base_fee() == 12345
    assert chain.w3.eth.fee_history_calls == [(1, "latest", [])]


def test_priority_fee_delegates_to_web3(chain_module) -> None:
    chain = Chain(1)

    assert chain.priority_fee() == 77
    assert chain.w3.eth.priority_fee_calls == 1


def test_get_transaction_delegates_to_web3(chain_module) -> None:
    chain = Chain(1)

    tx = chain.get_transaction("0xabc")

    assert tx == {"hash": "0xabc"}
    assert chain.w3.eth.tx_calls == ["0xabc"]


def test_get_block_uses_hash_method_for_bytes(monkeypatch, chain_module) -> None:
    chain = Chain(1)
    value = b"\x11" * 32

    def fake_hash32(v, name: str, strict: bool):
        assert v == value
        assert name == "block"
        assert strict is True
        return "0x" + "11" * 32

    monkeypatch.setattr(chain_module, "hash32", fake_hash32)

    block = chain.get_block(value)

    assert block == {"hash": "0x" + "11" * 32, "gasLimit": 999}
    assert chain.w3.eth.block_by_hash_calls == ["0x" + "11" * 32]
    assert chain.w3.eth.block_by_number_calls == []


def test_get_block_uses_hash_method_for_hash_string(monkeypatch, chain_module) -> None:
    chain = Chain(1)
    block_hash = "0x" + "ab" * 32

    def fake_block_ref(value, strict: bool):
        assert value == block_hash
        assert strict is True
        return block_hash

    monkeypatch.setattr(chain_module, "block_ref", fake_block_ref)

    block = chain.get_block(block_hash)

    assert block == {"hash": block_hash, "gasLimit": 999}
    assert chain.w3.eth.block_by_hash_calls == [block_hash]
    assert chain.w3.eth.block_by_number_calls == []


@pytest.mark.parametrize(
    ("block_identifier", "normalized", "expected_call"),
    [
        (7, "0x7", "0x7"),
        ("latest", "latest", "latest"),
        ("pending", "pending", "pending"),
    ],
)
def test_get_block_uses_number_method_for_non_hash_refs(
    monkeypatch,
    chain_module,
    block_identifier,
    normalized,
    expected_call,
) -> None:
    chain = Chain(1)

    def fake_block_ref(value, strict: bool):
        assert value == block_identifier
        assert strict is True
        return normalized

    monkeypatch.setattr(chain_module, "block_ref", fake_block_ref)

    block = chain.get_block(block_identifier)

    assert block["number"] == (int(normalized, 16) if normalized.startswith("0x") else normalized)
    assert chain.w3.eth.block_by_number_calls == [expected_call]
    assert chain.w3.eth.block_by_hash_calls == []


def test_as_default_sets_and_restores_default_chain(chain_module) -> None:
    chain = Chain(1)

    assert Chain._get_default_chain() == (None, False)

    with chain.as_default() as active:
        assert active is chain
        assert Chain._get_default_chain() == (chain, False)

    assert Chain._get_default_chain() == (None, False)


def test_as_default_restores_previous_chain_after_nested_contexts(chain_module) -> None:
    chain_a = Chain(1)
    chain_b = Chain(2)

    with chain_a.as_default():
        assert _default_chain() is chain_a

        with chain_b.as_default():
            assert _default_chain() is chain_b

        assert _default_chain() is chain_a

    assert _default_chain() is None


def test_as_default_strict_raises_on_mismatch(chain_module) -> None:
    chain_a = Chain(1)
    chain_b = Chain(2)

    with chain_a.as_default():
        with pytest.raises(ChainMismatch, match="default Chain context manager in strict mode"):
            with chain_b.as_default(strict=True):
                pass


def test_as_default_rejects_nesting_inside_existing_strict_context(chain_module) -> None:
    chain_a = Chain(1)
    chain_b = Chain(2)

    with chain_a.as_default(strict=True):
        with pytest.raises(
            ChainMismatch,
            match="cannot nest default Chain contexts inside strict mode",
        ):
            with chain_b.as_default():
                pass


def test_w3_property_rejects_access_outside_strict_default(chain_module) -> None:
    chain_a = Chain(1)
    chain_b = Chain(2)

    with chain_a.as_default(strict=True):
        assert chain_a.w3 is not None
        with pytest.raises(ChainMismatch, match="strict default chain"):
            _ = chain_b.w3


def test_default_chain_is_thread_local(chain_module) -> None:
    chain_main = Chain(1)
    chain_other = Chain(2)
    results: dict[str, Chain | None] = {}

    with chain_main.as_default():

        def worker() -> None:
            results["before"] = _default_chain()
            with chain_other.as_default():
                results["inside"] = _default_chain()
            results["after"] = _default_chain()

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        assert _default_chain() is chain_main

    assert results == {
        "before": None,
        "inside": chain_other,
        "after": None,
    }


def test_new_blocks_rejects_negative_height_buffer(chain_module) -> None:
    chain = Chain(1)

    with pytest.raises(ValueError, match="height_buffer must be >= 0"):
        next(chain.new_blocks(height_buffer=-1))


def test_new_blocks_rejects_non_positive_poll_interval(chain_module) -> None:
    chain = Chain(1)

    with pytest.raises(ValueError, match="poll_interval must be > 0"):
        next(chain.new_blocks(poll_interval=0))


def test_new_blocks_yields_only_when_buffered_height_changes(monkeypatch, chain_module) -> None:
    chain = Chain(1)
    heights = iter([10, 10, 12])
    block_calls: list[int] = []
    sleep_calls: list[float] = []
    monotonic_values = iter([100.0, 100.4, 105.0])

    monkeypatch.setattr(chain, "height", lambda: next(heights))

    def fake_get_block(number: int):
        block_calls.append(number)
        return {"number": number}

    monkeypatch.setattr(chain, "get_block", fake_get_block)
    monkeypatch.setattr(chain_module.time, "monotonic", lambda: next(monotonic_values))

    def fake_sleep(value: float) -> None:
        sleep_calls.append(value)

    monkeypatch.setattr(chain_module.time, "sleep", fake_sleep)

    generator = chain.new_blocks(height_buffer=1, poll_interval=5.0)
    block = next(generator)

    assert block == {"number": 10}
    assert block_calls == [10]
    assert sleep_calls == pytest.approx([4.6])


def test_new_blocks_sleeps_zero_when_loop_body_exceeds_poll_interval(
    monkeypatch, chain_module
) -> None:
    chain = Chain(1)
    heights = iter([5, 5, 7])
    sleep_calls: list[float] = []
    monotonic_values = iter([10.0, 15.5, 16.0])

    monkeypatch.setattr(chain, "height", lambda: next(heights))
    monkeypatch.setattr(chain, "get_block", lambda number: {"number": number})
    monkeypatch.setattr(chain_module.time, "monotonic", lambda: next(monotonic_values))

    def fake_sleep(value: float) -> None:
        sleep_calls.append(value)

    monkeypatch.setattr(chain_module.time, "sleep", fake_sleep)

    generator = chain.new_blocks(poll_interval=5.0)
    first = next(generator)
    second = next(generator)

    assert first == {"number": 6}
    assert second == {"number": 7}
    assert sleep_calls == [0]
