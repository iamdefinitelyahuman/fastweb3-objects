from __future__ import annotations

import json

import pytest

from fw3_objects import abi as abi_module
from fw3_objects.chain import Chain
from fw3_objects.contract import Contract, ContractCall, ContractTx, OverloadedMethod, _load_abi
from fw3_objects.explorers.lookup import HIGH_PRIORITY

ADDRESS = "0x" + "11" * 20
SENDER = "0x" + "22" * 20


class DummySender:
    def __init__(self, result="0x") -> None:
        self.result = result
        self.call_calls: list[dict[str, object]] = []
        self.estimate_gas_calls: list[dict[str, object]] = []
        self.transact_calls: list[dict[str, object]] = []

    def call(self, **kwargs):
        self.call_calls.append(kwargs)
        return self.result

    def estimate_gas(self, **kwargs):
        self.estimate_gas_calls.append(kwargs)
        return 12345

    def transact(self, **kwargs):
        self.transact_calls.append(kwargs)
        return "0x" + "aa" * 32


@pytest.fixture(autouse=True)
def reset_chain_state() -> None:
    Chain._instances.clear()
    Chain._set_default_chain(None, False)
    yield
    Chain._instances.clear()
    Chain._set_default_chain(None, False)


@pytest.fixture
def chain() -> Chain:
    return Chain(1)


def _returndata(outputs: list[dict], values: tuple) -> str:
    return "0x" + abi_module.encode(abi_module._abi_schema(outputs), values).hex()


def test_load_abi_accepts_list_and_artifact_file(tmp_path) -> None:
    abi = [{"type": "function", "name": "foo", "inputs": []}]
    list_path = tmp_path / "list.json"
    artifact_path = tmp_path / "artifact.json"
    list_path.write_text(json.dumps(abi))
    artifact_path.write_text(json.dumps({"abi": abi, "bytecode": "0x00"}))

    assert _load_abi(abi) == abi
    assert _load_abi(list_path) == abi
    assert _load_abi(str(artifact_path)) == abi


def test_load_abi_rejects_invalid_inputs(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text(json.dumps({"bytecode": "0x00"}))

    with pytest.raises(FileNotFoundError):
        _load_abi(missing)
    with pytest.raises(ValueError, match="Invalid ABI format"):
        _load_abi(invalid_json)
    with pytest.raises(ValueError, match="sequence of dicts"):
        _load_abi(["foo"])
    with pytest.raises(TypeError, match="abi must be"):
        _load_abi(object())


def test_contract_builds_call_and_tx_methods(chain) -> None:
    contract = Contract(
        ADDRESS,
        [
            {
                "type": "function",
                "name": "balanceOf",
                "stateMutability": "view",
                "inputs": [{"name": "owner", "type": "address"}],
                "outputs": [{"name": "balance", "type": "uint256"}],
            },
            {
                "type": "function",
                "name": "transfer",
                "stateMutability": "nonpayable",
                "inputs": [
                    {"name": "to", "type": "address"},
                    {"name": "amount", "type": "uint256"},
                ],
                "outputs": [{"name": "success", "type": "bool"}],
            },
        ],
        chain=chain,
    )

    assert isinstance(contract.balanceOf, ContractCall)
    assert isinstance(contract.transfer, ContractTx)
    assert str(contract.address) == ADDRESS


def test_contract_call_encodes_forwards_and_decodes_output(chain) -> None:
    outputs = [{"name": "balance", "type": "uint256"}]
    sender = DummySender(_returndata(outputs, (123,)))
    contract = Contract(
        ADDRESS,
        [
            {
                "type": "function",
                "name": "balanceOf",
                "stateMutability": "view",
                "inputs": [{"name": "owner", "type": "address"}],
                "outputs": outputs,
            }
        ],
        chain=chain,
    )

    result = contract.balanceOf(
        SENDER,
        sender=sender,
        value=5,
        gas_limit=50_000,
        block_identifier="safe",
    )

    assert result == 123
    assert sender.call_calls == [
        {
            "to": ADDRESS,
            "value": 5,
            "data": contract.balanceOf.encode_input(SENDER),
            "gas_limit": 50_000,
            "chain": chain,
            "block_identifier": "safe",
        }
    ]


def test_contract_estimate_gas_and_transact_forward_encoded_data(chain) -> None:
    sender = DummySender()
    contract = Contract(
        ADDRESS,
        [
            {
                "type": "function",
                "name": "transfer",
                "stateMutability": "nonpayable",
                "inputs": [
                    {"name": "to", "type": "address"},
                    {"name": "amount", "type": "uint256"},
                ],
                "outputs": [{"name": "success", "type": "bool"}],
            }
        ],
        chain=chain,
    )
    data = contract.transfer.encode_input(SENDER, 5)

    assert contract.transfer.estimate_gas(SENDER, 5, sender=sender, value=1) == 12345
    txid = contract.transfer(
        SENDER,
        5,
        sender=sender,
        value=1,
        gas_limit=50_000,
        gas_buffer=1.2,
        max_fee_per_gas=100,
        max_priority_fee_per_gas=2,
        nonce=7,
    )

    assert txid == "0x" + "aa" * 32
    assert sender.estimate_gas_calls == [
        {"to": contract.address, "value": 1, "data": data, "chain": chain}
    ]
    assert sender.transact_calls == [
        {
            "to": ADDRESS,
            "value": 1,
            "data": data,
            "gas_limit": 50_000,
            "gas_buffer": 1.2,
            "gas_price": None,
            "max_fee_per_gas": 100,
            "max_priority_fee_per_gas": 2,
            "chain": chain,
            "nonce": 7,
        }
    ]


def test_overloaded_method_resolves_by_argument_count_and_explicit_key(chain) -> None:
    contract = Contract(
        ADDRESS,
        [
            {
                "type": "function",
                "name": "foo",
                "stateMutability": "view",
                "inputs": [],
                "outputs": [],
            },
            {
                "type": "function",
                "name": "foo",
                "stateMutability": "view",
                "inputs": [{"name": "value", "type": "uint256"}],
                "outputs": [],
            },
            {
                "type": "function",
                "name": "bar",
                "stateMutability": "view",
                "inputs": [
                    {
                        "name": "value",
                        "type": "tuple",
                        "components": [{"name": "a", "type": "uint256"}],
                    }
                ],
                "outputs": [],
            },
        ],
        chain=chain,
    )

    assert isinstance(contract.foo, OverloadedMethod)
    assert contract.foo.signatures == ["foo()", "foo(uint256)"]
    assert contract.foo._resolve_by_args(()).signature == "foo()"
    assert contract.foo._resolve_by_args((1,)).signature == "foo(uint256)"
    assert contract.foo["uint256"].signature == "foo(uint256)"
    assert contract.foo[("uint256",)].signature == "foo(uint256)"


def test_overloaded_method_reports_no_match_and_ambiguous_match(chain) -> None:
    contract = Contract(
        ADDRESS,
        [
            {
                "type": "function",
                "name": "foo",
                "stateMutability": "view",
                "inputs": [{"name": "value", "type": "uint256"}],
                "outputs": [],
            },
            {
                "type": "function",
                "name": "foo",
                "stateMutability": "view",
                "inputs": [{"name": "value", "type": "address"}],
                "outputs": [],
            },
        ],
        chain=chain,
    )

    with pytest.raises(ValueError, match="Ambiguous overload"):
        contract.foo._resolve_by_args((1,))
    with pytest.raises(ValueError, match="No matching overload"):
        contract.foo._resolve_by_args((1, 2))
    with pytest.raises(ValueError, match="No overload"):
        contract.foo["bool"]
    with pytest.raises(TypeError, match="tuple must contain only strings"):
        contract.foo[(1,)]
    with pytest.raises(TypeError, match="comma-separated string"):
        contract.foo[1]


def test_contract_installs_cached_abi_without_fetching(monkeypatch, chain) -> None:
    cached_abi = [
        {
            "type": "function",
            "name": "name",
            "stateMutability": "view",
            "inputs": [],
            "outputs": [{"name": "", "type": "string"}],
        }
    ]

    class FakeCache:
        def get(self, chain_id, address, key):
            assert (chain_id, address) == (1, ADDRESS)
            if key == "abi":
                return cached_abi
            if key == "implementation":
                return None
            raise AssertionError(f"unexpected cache key: {key}")

        def set(self, chain_id, address, key, value):
            raise AssertionError("cached ABI should not be rewritten")

    monkeypatch.setattr("fw3_objects.contract.AddressMetadataCache", lambda: FakeCache())
    monkeypatch.setattr(
        "fw3_objects.contract.fetch_abi",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not fetch")),
    )

    contract = Contract(ADDRESS, chain=chain)

    assert contract.abi == cached_abi
    assert isinstance(contract.name, ContractCall)


def test_contract_refresh_false_does_not_fetch_missing_abi(monkeypatch, chain) -> None:
    class FakeCache:
        def get(self, chain_id, address, key):
            return None

        def set(self, chain_id, address, key, value):
            raise AssertionError("missing ABI should not be cached")

    monkeypatch.setattr("fw3_objects.contract.AddressMetadataCache", lambda: FakeCache())
    monkeypatch.setattr(
        "fw3_objects.contract.fetch_abi",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not fetch")),
    )

    contract = Contract(ADDRESS, chain=chain, refresh_abi=False)

    with pytest.raises(AttributeError):
        contract.abi
    with pytest.raises(AttributeError):
        contract.name


def test_contract_explicit_abi_cache_write_rules(monkeypatch, chain) -> None:
    abi = [
        {
            "type": "function",
            "name": "name",
            "stateMutability": "view",
            "inputs": [],
            "outputs": [],
        }
    ]
    writes = []

    class FakeCache:
        def __init__(self, cached):
            self.cached = cached

        def get(self, chain_id, address, key):
            return self.cached

        def set(self, chain_id, address, key, value):
            writes.append((chain_id, address, key, value))

    cache = FakeCache(cached=None)
    monkeypatch.setattr("fw3_objects.contract.AddressMetadataCache", lambda: cache)

    Contract(ADDRESS, abi, chain=chain)
    assert writes == [(1, ADDRESS, "abi", abi)]

    cache.cached = [{"type": "function", "name": "old", "inputs": [], "outputs": []}]
    Contract(ADDRESS, abi, chain=chain)
    assert writes == [(1, ADDRESS, "abi", abi)]

    Contract(ADDRESS, abi, chain=chain, refresh_abi=True)
    assert writes == [(1, ADDRESS, "abi", abi), (1, ADDRESS, "abi", abi)]


def test_contract_async_abi_lookup_caches_on_success_and_installs_on_access(
    monkeypatch, chain
) -> None:
    abi = [
        {
            "type": "function",
            "name": "name",
            "stateMutability": "view",
            "inputs": [],
            "outputs": [],
        }
    ]
    writes = []
    fetch_calls = []

    class FakeCache:
        def get(self, chain_id, address, key):
            return None

        def set(self, chain_id, address, key, value):
            writes.append((chain_id, address, key, value))

    class FakeJob:
        def __init__(self):
            self.priority = None

        def bump_priority(self, priority):
            self.priority = priority

        def wait(self):
            return abi, None

    job = FakeJob()

    def fake_fetch_abi(chain_id, address, **kwargs):
        fetch_calls.append((chain_id, address, kwargs["ignore_negative_cache"]))
        kwargs["on_success"]((abi, None))
        return job

    monkeypatch.setattr("fw3_objects.contract.AddressMetadataCache", lambda: FakeCache())
    monkeypatch.setattr("fw3_objects.contract.fetch_abi", fake_fetch_abi)

    contract = Contract(ADDRESS, chain=chain)

    assert fetch_calls == [(1, ADDRESS, False)]
    assert writes == [(1, ADDRESS, "abi", abi)]
    assert isinstance(contract.name, ContractCall)
    assert job.priority == HIGH_PRIORITY
    assert contract.abi == abi


def test_contract_without_chain_uses_default_or_raises() -> None:
    abi = [
        {"type": "function", "name": "foo", "stateMutability": "view", "inputs": [], "outputs": []}
    ]

    from fw3_objects.errors import NoActiveChain

    with pytest.raises(NoActiveChain, match="No chain specified"):
        Contract(ADDRESS, abi)

    default = Chain(1)
    Chain._set_default_chain(default, False)

    contract = Contract(ADDRESS, abi)

    assert contract.foo.chain is default


def test_contract_str_returns_address(chain) -> None:
    contract = Contract(ADDRESS, [], chain=chain)

    assert str(contract) == ADDRESS


def test_contract_rejects_reserved_function_names(chain) -> None:
    with pytest.raises(ValueError, match="reserved attribute 'abi'"):
        Contract(
            ADDRESS,
            [
                {
                    "type": "function",
                    "name": "abi",
                    "stateMutability": "view",
                    "inputs": [],
                    "outputs": [],
                }
            ],
            chain=chain,
        )


def test_contract_legacy_mutability_flags_install_correct_method_classes(chain) -> None:
    contract = Contract(
        ADDRESS,
        [
            {
                "type": "function",
                "name": "legacyView",
                "constant": True,
                "inputs": [],
                "outputs": [],
            },
            {
                "type": "function",
                "name": "legacyPayable",
                "payable": True,
                "inputs": [],
                "outputs": [],
            },
            {"type": "function", "name": "legacyTx", "inputs": [], "outputs": []},
        ],
        chain=chain,
    )

    assert isinstance(contract.legacyView, ContractCall)
    assert isinstance(contract.legacyPayable, ContractTx)
    assert isinstance(contract.legacyTx, ContractTx)
    assert contract.legacyView.mutability == "view"
    assert contract.legacyPayable.mutability == "payable"
    assert contract.legacyTx.mutability == "nonpayable"


def test_contract_method_selector_and_decode_input(chain) -> None:
    contract = Contract(
        ADDRESS,
        [
            {
                "type": "function",
                "name": "transfer",
                "stateMutability": "nonpayable",
                "inputs": [
                    {"name": "to", "type": "address"},
                    {"name": "amount", "type": "uint256"},
                ],
                "outputs": [],
            }
        ],
        chain=chain,
    )
    calldata = contract.transfer.encode_input(SENDER, 5)

    assert contract.transfer.selector == abi_module.function_selector(contract.transfer.method_abi)
    assert contract.transfer.decode_input(calldata) == (SENDER, 5)


def test_contract_unresolved_attribute_after_abi_resolution_raises_attribute_error(
    monkeypatch, chain
) -> None:
    abi = [
        {"type": "function", "name": "name", "stateMutability": "view", "inputs": [], "outputs": []}
    ]

    class FakeCache:
        def get(self, chain_id, address, key):
            return None

        def set(self, chain_id, address, key, value):
            pass

    class FakeJob:
        def bump_priority(self, priority):
            pass

        def wait(self):
            return abi, None

    monkeypatch.setattr("fw3_objects.contract.AddressMetadataCache", lambda: FakeCache())
    monkeypatch.setattr("fw3_objects.contract.fetch_abi", lambda *args, **kwargs: FakeJob())

    contract = Contract(ADDRESS, chain=chain)

    with pytest.raises(AttributeError, match="missing"):
        contract.missing

    assert contract.abi == abi


def test_contract_async_abi_lookup_reraises_missing_abi_without_implementation(
    monkeypatch, chain
) -> None:
    from fw3_objects.errors import ABINotFound

    class FakeCache:
        def get(self, chain_id, address, key):
            return None

        def set(self, chain_id, address, key, value):
            raise AssertionError("missing ABI should not be cached")

    class FakeJob:
        def __init__(self):
            self.priority = None

        def bump_priority(self, priority):
            self.priority = priority

        def wait(self):
            raise ABINotFound

    job = FakeJob()
    monkeypatch.setattr("fw3_objects.contract.AddressMetadataCache", lambda: FakeCache())
    monkeypatch.setattr("fw3_objects.contract.fetch_abi", lambda *args, **kwargs: job)

    contract = Contract(ADDRESS, chain=chain)

    with pytest.raises(ABINotFound):
        contract.abi

    assert job.priority == HIGH_PRIORITY


def test_contract_missing_proxy_abi_with_forced_implementation_uses_implementation_abi(
    monkeypatch, chain
) -> None:
    from fw3_objects.errors import ABINotFound

    implementation = "0x" + "33" * 20
    implementation_abi = [
        {
            "type": "function",
            "name": "implOnly",
            "stateMutability": "view",
            "inputs": [],
            "outputs": [],
        }
    ]

    class FakeCache:
        def get(self, chain_id, address, key):
            if address == implementation and key == "abi":
                return implementation_abi
            return None

        def set(self, chain_id, address, key, value):
            pass

    class FakeJob:
        def bump_priority(self, priority):
            pass

        def wait(self):
            raise ABINotFound

    monkeypatch.setattr("fw3_objects.contract.AddressMetadataCache", lambda: FakeCache())
    monkeypatch.setattr("fw3_objects.contract.fetch_abi", lambda *args, **kwargs: FakeJob())

    contract = Contract(ADDRESS, chain=chain, implementation=implementation)

    assert contract.abi == implementation_abi
    assert isinstance(contract.implOnly, ContractCall)


def test_contract_cached_proxy_abi_overlays_cached_implementation_abi(chain, monkeypatch) -> None:
    implementation = "0x" + "33" * 20
    proxy_abi = [
        {
            "type": "function",
            "name": "proxyOnly",
            "stateMutability": "view",
            "inputs": [],
            "outputs": [],
        }
    ]
    implementation_abi = [
        {
            "type": "function",
            "name": "implOnly",
            "stateMutability": "view",
            "inputs": [],
            "outputs": [],
        }
    ]

    class FakeCache:
        def get(self, chain_id, address, key):
            if address == ADDRESS and key == "abi":
                return proxy_abi
            if address == ADDRESS and key == "implementation":
                return implementation
            if address == implementation and key == "abi":
                return implementation_abi
            return None

        def set(self, chain_id, address, key, value):
            raise AssertionError("cached ABI should not be rewritten")

    monkeypatch.setattr("fw3_objects.contract.AddressMetadataCache", lambda: FakeCache())
    monkeypatch.setattr(
        "fw3_objects.contract.fetch_abi",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not fetch")),
    )

    contract = Contract(ADDRESS, chain=chain)

    assert isinstance(contract.implOnly, ContractCall)
    assert isinstance(contract.proxyOnly, ContractCall)
    assert contract.abi == implementation_abi + proxy_abi


def test_contract_forced_implementation_starts_lookup_and_fetches_proxy_abi(
    monkeypatch, chain
) -> None:
    implementation = "0x" + "33" * 20
    proxy_abi = [
        {
            "type": "function",
            "name": "proxyOnly",
            "stateMutability": "view",
            "inputs": [],
            "outputs": [],
        }
    ]
    implementation_abi = [
        {
            "type": "function",
            "name": "implOnly",
            "stateMutability": "view",
            "inputs": [],
            "outputs": [],
        }
    ]
    fetch_calls = []

    class FakeCache:
        def get(self, chain_id, address, key):
            if address == implementation and key == "abi":
                return implementation_abi
            return None

        def set(self, chain_id, address, key, value):
            pass

    class FakeJob:
        def bump_priority(self, priority):
            pass

        def wait(self):
            return proxy_abi, None

    def fake_fetch_abi(chain_id, address, **kwargs):
        fetch_calls.append((address, kwargs["resolve_proxy"]))
        return FakeJob()

    monkeypatch.setattr("fw3_objects.contract.AddressMetadataCache", lambda: FakeCache())
    monkeypatch.setattr("fw3_objects.contract.fetch_abi", fake_fetch_abi)

    contract = Contract(ADDRESS, chain=chain, implementation=implementation)

    assert fetch_calls == [(ADDRESS, False)]
    assert isinstance(contract.implOnly, ContractCall)
    assert isinstance(contract.proxyOnly, ContractCall)


def test_contract_fetch_success_with_detected_implementation_starts_implementation_lookup(
    monkeypatch, chain
) -> None:
    implementation = "0x" + "33" * 20
    proxy_abi = [
        {
            "type": "function",
            "name": "proxyOnly",
            "stateMutability": "view",
            "inputs": [],
            "outputs": [],
        }
    ]
    implementation_abi = [
        {
            "type": "function",
            "name": "implOnly",
            "stateMutability": "view",
            "inputs": [],
            "outputs": [],
        }
    ]

    class FakeCache:
        def get(self, chain_id, address, key):
            if address == implementation and key == "abi":
                return implementation_abi
            return None

        def set(self, chain_id, address, key, value):
            pass

    class FakeJob:
        def bump_priority(self, priority):
            pass

        def wait(self):
            return proxy_abi, implementation

    monkeypatch.setattr("fw3_objects.contract.AddressMetadataCache", lambda: FakeCache())
    monkeypatch.setattr("fw3_objects.contract.fetch_abi", lambda *args, **kwargs: FakeJob())

    contract = Contract(ADDRESS, chain=chain)

    assert isinstance(contract.implOnly, ContractCall)
    assert isinstance(contract.proxyOnly, ContractCall)


def test_overloaded_method_empty_key_selects_no_arg_overload(chain) -> None:
    contract = Contract(
        ADDRESS,
        [
            {
                "type": "function",
                "name": "foo",
                "stateMutability": "view",
                "inputs": [],
                "outputs": [],
            },
            {
                "type": "function",
                "name": "foo",
                "stateMutability": "view",
                "inputs": [{"name": "value", "type": "uint256"}],
                "outputs": [],
            },
        ],
        chain=chain,
    )

    assert contract.foo[""].signature == "foo()"


def test_overloaded_method_call_estimate_transact_and_dunder_call_forward_to_resolved_method(
    chain,
) -> None:
    outputs = [{"name": "value", "type": "uint256"}]
    sender = DummySender(_returndata(outputs, (123,)))
    contract = Contract(
        ADDRESS,
        [
            {
                "type": "function",
                "name": "foo",
                "stateMutability": "view",
                "inputs": [],
                "outputs": outputs,
            },
            {
                "type": "function",
                "name": "foo",
                "stateMutability": "nonpayable",
                "inputs": [{"name": "value", "type": "uint256"}],
                "outputs": [],
            },
        ],
        chain=chain,
    )

    assert contract.foo.call(sender=sender, block_identifier="latest") == 123
    assert contract.foo(sender=sender, block_identifier="safe") == 123
    assert contract.foo.estimate_gas(1, sender=sender, value=2) == 12345
    assert contract.foo(1, sender=sender, value=2, gas_limit=30_000, nonce=4) == "0x" + "aa" * 32

    assert len(sender.call_calls) == 2
    assert sender.call_calls[0]["block_identifier"] == "latest"
    assert sender.call_calls[1]["block_identifier"] == "safe"
    assert sender.estimate_gas_calls == [
        {
            "to": contract.address,
            "value": 2,
            "data": contract.foo["uint256"].encode_input(1),
            "chain": chain,
        }
    ]
    assert sender.transact_calls == [
        {
            "to": ADDRESS,
            "value": 2,
            "data": contract.foo["uint256"].encode_input(1),
            "gas_limit": 30_000,
            "gas_buffer": None,
            "gas_price": None,
            "max_fee_per_gas": None,
            "max_priority_fee_per_gas": None,
            "chain": chain,
            "nonce": 4,
        }
    ]
