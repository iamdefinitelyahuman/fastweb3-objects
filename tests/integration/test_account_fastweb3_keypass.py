from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import fw3_keypass.crypto as kp_crypto
import httpx
import pytest

from fw3_objects.account import Account, Accounts
from fw3_objects.chain import Chain, configure_chain

CHAIN_ID = 1
RPC_URL = "http://rpc.test"
LATEST_BLOCK = 123
BALANCE = 456_789
NONCE = 7
CALL_RESULT = "0x1234"
ESTIMATED_GAS = 21_000
BASE_FEE = 1_000_000_000
PRIORITY_FEE = 2_000_000_000
TX_HASH = "0x" + "aa" * 32
RECIPIENT = "0x" + "22" * 20


class DummyMonitor:
    def watch(self, tx) -> None:
        pass


@pytest.fixture(autouse=True)
def reset_chain_state() -> None:
    Chain._instances.clear()
    Chain._set_default_chain(None, False)
    yield
    Chain._instances.clear()
    Chain._set_default_chain(None, False)


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        kp_crypto,
        "DEFAULT_KDF_PARAMS",
        {
            "time_cost": 1,
            "memory_cost": 8 * 1024,
            "parallelism": 1,
            "length": 32,
        },
    )


@pytest.fixture
def rpc_recorder(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    recorded_requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        calls = payload if isinstance(payload, list) else [payload]
        recorded_requests.extend(calls)
        body = [_rpc_response(call) for call in calls]
        return httpx.Response(200, json=body if isinstance(payload, list) else body[0])

    original_client = httpx.Client

    class MockClient(original_client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", MockClient)
    return recorded_requests


@pytest.fixture
def configured_chain(
    rpc_recorder: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
):
    fw3_web3 = importlib.import_module("fw3.web3.web3")

    def fail_if_pool_requested(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("acquire_pool_manager should not be called when use_public_pool=False")

    monkeypatch.setattr(fw3_web3, "acquire_pool_manager", fail_if_pool_requested)

    configure_chain(
        CHAIN_ID,
        endpoints=[RPC_URL],
        use_public_pool=False,
    )

    chain = Chain(CHAIN_ID)

    yield chain, rpc_recorder

    chain.w3.close()


@pytest.fixture
def accounts_db(tmp_path: Path) -> Accounts:
    path = tmp_path / "accounts.sqlite3"
    accounts = Accounts(path, create=True, password="pw")
    yield accounts
    accounts.close()


def test_accounts_database_round_trip_with_real_keypass(accounts_db, tmp_path: Path) -> None:
    created = accounts_db.create_account(alias="alice")

    assert isinstance(created, Account)
    assert created.can_sign is True
    assert accounts_db["alice"].address == created.address
    assert accounts_db.default_account.address == created.address

    path = accounts_db.path
    accounts_db.close()

    reopened = Accounts(path, password="pw")
    try:
        loaded = reopened["alice"]
        assert isinstance(loaded, Account)
        assert loaded.address == created.address
        assert repr(reopened) == "<Accounts 'accounts' unlocked>"
    finally:
        reopened.close()


def test_account_methods_work_against_fastweb3_and_keypass(
    configured_chain,
    accounts_db: Accounts,
) -> None:
    chain, rpc_calls = configured_chain
    chain._Chain__transaction_monitor = DummyMonitor()
    account = accounts_db.create_account(alias="alice").on(chain)

    assert account.balance() == BALANCE
    assert account.balance(block_identifier="pending") == BALANCE
    assert account.nonce() == NONCE
    assert account.nonce(block_identifier="pending") == NONCE
    assert account.call(to=RECIPIENT, data="0x1234") == CALL_RESULT
    assert account.estimate_gas(to=RECIPIENT, value=1) == ESTIMATED_GAS
    tx = account.transact(to=RECIPIENT, value=1)
    assert tx.hash == TX_HASH

    methods = [call["method"] for call in rpc_calls]
    assert methods == [
        "eth_blockNumber",
        "eth_getBalance",
        "eth_blockNumber",
        "eth_getBalance",
        "eth_blockNumber",
        "eth_getTransactionCount",
        "eth_blockNumber",
        "eth_getTransactionCount",
        "eth_blockNumber",
        "eth_call",
        "eth_blockNumber",
        "eth_estimateGas",
        "eth_blockNumber",
        "eth_getTransactionCount",
        "eth_estimateGas",
        "eth_maxPriorityFeePerGas",
        "eth_feeHistory",
        "eth_blockNumber",
        "eth_sendRawTransaction",
    ]

    normalized_address = account.address.lower()

    assert rpc_calls[1]["params"] == [normalized_address, "latest"]
    assert rpc_calls[3]["params"] == [normalized_address, "pending"]
    assert rpc_calls[5]["params"] == [normalized_address, "latest"]
    assert rpc_calls[7]["params"] == [normalized_address, "pending"]
    assert rpc_calls[9]["params"] == [
        {
            "from": normalized_address,
            "to": RECIPIENT.lower(),
            "data": "0x1234",
            "chainId": hex(CHAIN_ID),
        },
        "latest",
    ]
    assert rpc_calls[11]["params"] == [
        {
            "from": normalized_address,
            "to": RECIPIENT.lower(),
            "value": hex(1),
            "chainId": hex(CHAIN_ID),
        }
    ]
    assert rpc_calls[14]["params"] == [
        {
            "from": normalized_address,
            "to": RECIPIENT.lower(),
            "value": hex(1),
            "chainId": hex(CHAIN_ID),
        }
    ]
    assert rpc_calls[15]["params"] == []
    assert rpc_calls[16]["params"] == [hex(1), "latest", []]
    assert isinstance(rpc_calls[18]["params"][0], str)
    assert rpc_calls[18]["params"][0].startswith("0x02")


def _rpc_response(call: dict[str, Any]) -> dict[str, Any]:
    method = call["method"]

    if method == "eth_blockNumber":
        result: Any = hex(LATEST_BLOCK)
    elif method == "eth_getBalance":
        result = hex(BALANCE)
    elif method == "eth_getTransactionCount":
        result = hex(NONCE)
    elif method == "eth_call":
        result = CALL_RESULT
    elif method == "eth_estimateGas":
        result = hex(ESTIMATED_GAS)
    elif method == "eth_maxPriorityFeePerGas":
        result = hex(PRIORITY_FEE)
    elif method == "eth_feeHistory":
        result = {
            "oldestBlock": hex(LATEST_BLOCK),
            "baseFeePerGas": [hex(BASE_FEE), hex(BASE_FEE + 1)],
            "gasUsedRatio": [0.5],
            "reward": [],
        }
    elif method == "eth_sendRawTransaction":
        result = TX_HASH
    else:
        raise AssertionError(f"Unexpected RPC method: {method}")

    return {"jsonrpc": "2.0", "id": call["id"], "result": result}
