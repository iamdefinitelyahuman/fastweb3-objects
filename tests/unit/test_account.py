from __future__ import annotations

from pathlib import Path

import pytest

from fw3_objects.account import Account, Accounts
from fw3_objects.chain import Chain
from fw3_objects.errors import ChainMismatch, NoActiveChain


class DummyEth:
    def __init__(self) -> None:
        self.balance_calls: list[tuple[str, object]] = []
        self.nonce_calls: list[tuple[str, object]] = []
        self.call_calls: list[dict[str, object]] = []
        self.estimate_gas_calls: list[dict[str, object]] = []
        self.send_raw_transaction_calls: list[bytes] = []

    def get_balance(self, address: str, block: object = "latest") -> int:
        self.balance_calls.append((address, block))
        return 123

    def get_transaction_count(self, address: str, block: object = "latest") -> int:
        self.nonce_calls.append((address, block))
        return 7

    def call(self, **kwargs):
        self.call_calls.append(kwargs)
        return "0xdeadbeef"

    def estimate_gas(self, **kwargs):
        self.estimate_gas_calls.append(kwargs)
        return 21_001

    def send_raw_transaction(self, raw_tx: bytes) -> str:
        self.send_raw_transaction_calls.append(raw_tx)
        return "0x" + "aa" * 32


class DummyBatch:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class DummyWeb3:
    def __init__(self) -> None:
        self.eth = DummyEth()

    def batch_requests(self) -> DummyBatch:
        return DummyBatch()


@pytest.fixture(autouse=True)
def reset_chain_state() -> None:
    Chain._instances.clear()
    Chain._set_default_chain(None, False)
    yield
    Chain._instances.clear()
    Chain._set_default_chain(None, False)


@pytest.fixture
def chain(monkeypatch):
    chain = Chain(1)
    chain._w3 = DummyWeb3()
    return chain


@pytest.fixture
def account(chain) -> Account:
    return Account("0x" + "11" * 20, chain=chain)


def test_accounts_creates_new_default_db_when_missing(monkeypatch, tmp_path: Path) -> None:
    default_path = tmp_path / "default.sqlite3"
    initialize_calls: list[str] = []
    unlock_calls: list[object] = []

    monkeypatch.setattr("fw3_objects.account.resolve_db_path", lambda value=None: default_path)
    monkeypatch.setattr(
        "fw3_keypass.db.base.BaseKeypassDB.__init__",
        lambda self, path: setattr(self, "path", Path(path)),
    )
    monkeypatch.setattr(
        Accounts, "initialize", lambda self, password: initialize_calls.append(password)
    )
    monkeypatch.setattr(
        Accounts, "unlock", lambda self, password=None: unlock_calls.append(password)
    )
    monkeypatch.setattr(Accounts, "is_unlocked", property(lambda self: True))

    accounts = Accounts(password="pw")

    assert accounts.path == default_path
    assert accounts._is_default is True
    assert initialize_calls == ["pw"]
    assert unlock_calls == []


def test_accounts_opens_existing_db_and_unlocks(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "named.sqlite3"
    path.touch()
    initialize_calls: list[str] = []
    unlock_calls: list[object] = []

    monkeypatch.setattr(
        "fw3_keypass.db.base.BaseKeypassDB.__init__",
        lambda self, path: setattr(self, "path", Path(path)),
    )
    monkeypatch.setattr(
        Accounts, "initialize", lambda self, password: initialize_calls.append(password)
    )
    monkeypatch.setattr(
        Accounts, "unlock", lambda self, password=None: unlock_calls.append(password)
    )
    monkeypatch.setattr(Accounts, "is_unlocked", property(lambda self: False))

    accounts = Accounts(path, password="pw")

    assert accounts.path == path
    assert accounts._is_default is False
    assert initialize_calls == []
    assert unlock_calls == ["pw"]
    assert repr(accounts) == "<Accounts 'named' locked>"


def test_accounts_missing_named_db_requires_create(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "missing.sqlite3"
    monkeypatch.setattr(
        "fw3_keypass.db.base.BaseKeypassDB.__init__",
        lambda self, path: setattr(self, "path", Path(path)),
    )

    with pytest.raises(FileNotFoundError, match="Database does not exist"):
        Accounts(path, create=False)


def test_accounts_make_account_returns_subclass(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    monkeypatch.setattr(
        "fw3_keypass.db.base.BaseKeypassDB.__init__",
        lambda self, path: setattr(self, "path", Path(path)),
    )
    monkeypatch.setattr(Accounts, "initialize", lambda self, password: None)
    monkeypatch.setattr(Accounts, "unlock", lambda self, password=None: None)
    monkeypatch.setattr(Accounts, "is_unlocked", property(lambda self: True))

    accounts = Accounts(path, create=True, password="pw")
    acct = accounts._make_account("0x" + "12" * 20)

    assert isinstance(acct, Account)
    assert acct._db is accounts


def test_on_returns_new_account_bound_to_new_chain(account) -> None:
    rebound = account.on(2)

    assert isinstance(rebound, Account)
    assert rebound is not account
    assert rebound.address == account.address
    assert int(rebound._bound_chain) == 2


def test_balance_and_nonce_delegate_to_web3(account, chain) -> None:
    assert account.balance() == 123
    assert account.balance(block_identifier="pending") == 123
    assert account.nonce() == 7
    assert account.nonce(block_identifier=12) == 7

    assert chain.w3.eth.balance_calls == [
        (account.address, "latest"),
        (account.address, "pending"),
    ]
    assert chain.w3.eth.nonce_calls == [
        (account.address, "latest"),
        (account.address, 12),
    ]


def test_call_omits_none_kwargs(account, chain) -> None:
    result = account.call(to="0x" + "22" * 20, data="0x1234")

    assert result == "0xdeadbeef"
    assert chain.w3.eth.call_calls == [
        {
            "from_": account.address,
            "to": "0x" + "22" * 20,
            "data": "0x1234",
            "chain_id": 1,
        }
    ]


def test_call_includes_optional_kwargs(account, chain) -> None:
    account.call(
        to="0x" + "22" * 20,
        value=5,
        gas_limit=50_000,
        block_identifier="safe",
    )

    assert chain.w3.eth.call_calls == [
        {
            "from_": account.address,
            "to": "0x" + "22" * 20,
            "gas": 50_000,
            "value": 5,
            "chain_id": 1,
            "block": "safe",
        }
    ]


def test_estimate_gas_omits_none_kwargs(account, chain) -> None:
    result = account.estimate_gas(to="0x" + "22" * 20, value=9)

    assert result == 21_001
    assert chain.w3.eth.estimate_gas_calls == [
        {
            "from_": account.address,
            "to": "0x" + "22" * 20,
            "value": 9,
            "chain_id": 1,
        }
    ]


def test_transact_builds_signs_and_sends_transaction(monkeypatch, account, chain) -> None:
    build_calls: list[dict[str, object]] = []

    def fake_build_transaction_object(**kwargs):
        build_calls.append(kwargs)
        return {"tx": "object"}

    class Signed:
        raw_transaction = b"\xde\xad"

    monkeypatch.setattr(
        "fw3_objects.account.build_transaction_object", fake_build_transaction_object
    )
    monkeypatch.setattr(account, "sign_transaction", lambda tx: Signed())
    monkeypatch.setattr(account, "nonce", lambda **kwargs: 8)
    monkeypatch.setattr(account, "estimate_gas", lambda **kwargs: 21_001)
    monkeypatch.setattr(chain, "priority_fee", lambda: 3)
    monkeypatch.setattr(chain, "base_fee", lambda: 100)

    txid = account.transact(to="0x" + "22" * 20, value=1, gas_buffer=1.2)

    assert txid == "0x" + "aa" * 32
    assert build_calls == [
        {
            "from_": account.address,
            "to": "0x" + "22" * 20,
            "gas": 25_201,
            "gas_price": None,
            "max_fee_per_gas": 125,
            "max_priority_fee_per_gas": 3,
            "value": 1,
            "data": None,
            "nonce": 8,
            "chain_id": 1,
        }
    ]
    assert chain.w3.eth.send_raw_transaction_calls == [b"\xde\xad"]


def test_transact_uses_explicit_legacy_gas_price(monkeypatch, account, chain) -> None:
    build_calls: list[dict[str, object]] = []

    def fake_build_transaction_object(**kwargs):
        build_calls.append(kwargs)
        return {"tx": "object"}

    class Signed:
        raw_transaction = b"\xbe\xef"

    monkeypatch.setattr(
        "fw3_objects.account.build_transaction_object", fake_build_transaction_object
    )
    monkeypatch.setattr(account, "sign_transaction", lambda tx: Signed())

    txid = account.transact(
        to="0x" + "22" * 20,
        gas_limit=21_000,
        gas_price=99,
        nonce=4,
    )

    assert txid == "0x" + "aa" * 32
    assert build_calls == [
        {
            "from_": account.address,
            "to": "0x" + "22" * 20,
            "gas": 21_000,
            "gas_price": 99,
            "max_fee_per_gas": None,
            "max_priority_fee_per_gas": None,
            "value": None,
            "data": None,
            "nonce": 4,
            "chain_id": 1,
        }
    ]
    assert chain.w3.eth.send_raw_transaction_calls == [b"\xbe\xef"]


def test_transact_rejects_small_gas_buffer(account) -> None:
    with pytest.raises(ValueError, match="Gas buffer must be at least 1"):
        account.transact(gas_limit=21_000, gas_buffer=0.99, nonce=1, gas_price=1)


def test_get_deployment_address_with_explicit_nonce(account) -> None:
    assert account.get_deployment_address(nonce=0) == "0x8F7a45eBDe059392E46A46DCc14AB24681A961Ea"
    assert account.get_deployment_address(nonce=3) == "0xb35b8b030a4BC592Ea8ccf3684512CE083f108dC"


def test_get_deployment_address_uses_account_nonce(monkeypatch, account) -> None:
    monkeypatch.setattr(account, "nonce", lambda **kwargs: 1)

    assert account.get_deployment_address() == "0x15452EC016c4dc8c549E7fe6Ff4b26324Ea8b7A4"


def test_get_deployment_address_validates_nonce_type_and_sign(account) -> None:
    with pytest.raises(TypeError, match="nonce must be an int"):
        account.get_deployment_address(nonce="1")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="nonce cannot be negative"):
        account.get_deployment_address(nonce=-1)


def test_resolve_chain_uses_explicit_chain_for_unbound_account(chain) -> None:
    account = Account("0x" + "11" * 20)

    assert account._resolve_chain(1) is chain


def test_resolve_chain_uses_default_chain_for_unbound_account(chain) -> None:
    account = Account("0x" + "11" * 20)

    with chain.as_default():
        assert account._resolve_chain(None) is chain


def test_resolve_chain_rejects_missing_chain_for_unbound_account() -> None:
    account = Account("0x" + "11" * 20)

    with pytest.raises(NoActiveChain, match="No chain specified for unbound Account"):
        account._resolve_chain(None)


def test_resolve_chain_rejects_mismatched_explicit_chain(account) -> None:
    with pytest.raises(ChainMismatch, match="bound Account"):
        account._resolve_chain(2)
