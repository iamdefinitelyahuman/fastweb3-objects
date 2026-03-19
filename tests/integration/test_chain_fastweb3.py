from __future__ import annotations

import importlib
import json

import httpx
import pytest

from fw3_objects.chain import Chain, configure_chain

CHAIN_ID = 31_337
RPC_URL = "http://rpc.test"
LATEST_BLOCK = 123
BLOCK_HASH = "0x" + "11" * 32
TX_HASH = "0x" + "22" * 32
TX_FROM = "0x" + "33" * 20
TX_TO = "0x" + "44" * 20
BASE_FEE = 1_000_000_000
PRIORITY_FEE = 2_000_000_000
BLOCK_GAS_LIMIT = 30_000_000


@pytest.fixture(autouse=True)
def reset_chain_state() -> None:
    Chain._instances.clear()
    Chain._set_default_chain(None, False)
    yield
    Chain._instances.clear()
    Chain._set_default_chain(None, False)


@pytest.fixture
def rpc_recorder(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    recorded_requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        calls = payload if isinstance(payload, list) else [payload]
        recorded_requests.extend(calls)
        body = [_rpc_response(call) for call in calls]
        return httpx.Response(200, json=body if isinstance(payload, list) else body[0])

    original_client = httpx.Client

    class MockClient(original_client):
        def __init__(self, *args, **kwargs) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", MockClient)
    return recorded_requests


@pytest.fixture
def configured_chain(
    rpc_recorder: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
):
    fw3_web3 = importlib.import_module("fw3.web3.web3")

    def fail_if_pool_requested(*args, **kwargs) -> None:
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


def test_configure_chain_uses_fastweb3_without_public_pool(configured_chain) -> None:
    chain, rpc_calls = configured_chain

    assert chain.w3.active_pool_size() == 1
    assert chain.w3.pool_capacity() == 1
    assert chain.height() == LATEST_BLOCK
    assert [call["method"] for call in rpc_calls] == ["eth_blockNumber", "eth_blockNumber"]


def test_chain_methods_are_wired_into_fastweb3_web3(configured_chain) -> None:
    chain, rpc_calls = configured_chain

    assert chain.height() == LATEST_BLOCK
    assert chain.block_gas_limit() == BLOCK_GAS_LIMIT
    assert chain.base_fee() == BASE_FEE
    assert chain.priority_fee() == PRIORITY_FEE

    tx = chain.get_transaction(TX_HASH)
    assert tx is not None
    assert tx["hash"] == TX_HASH
    assert tx["blockNumber"] == LATEST_BLOCK
    assert tx["from"] == TX_FROM
    assert tx["to"] == TX_TO

    block = chain.get_block(BLOCK_HASH)
    assert block is not None
    assert block["hash"] == BLOCK_HASH
    assert block["number"] == LATEST_BLOCK
    assert block["gasLimit"] == BLOCK_GAS_LIMIT
    assert block["baseFeePerGas"] == BASE_FEE

    assert [call["method"] for call in rpc_calls] == [
        "eth_blockNumber",
        "eth_blockNumber",
        "eth_blockNumber",
        "eth_getBlockByNumber",
        "eth_blockNumber",
        "eth_feeHistory",
        "eth_blockNumber",
        "eth_maxPriorityFeePerGas",
        "eth_blockNumber",
        "eth_getTransactionByHash",
        "eth_blockNumber",
        "eth_getBlockByHash",
    ]

    assert rpc_calls[3]["params"] == ["latest", False]
    assert rpc_calls[5]["params"] == [hex(1), "latest", []]
    assert rpc_calls[9]["params"] == [TX_HASH]
    assert rpc_calls[11]["params"] == [BLOCK_HASH, False]


def _rpc_response(call: dict[str, object]) -> dict[str, object]:
    method = call["method"]

    if method == "eth_blockNumber":
        result: object = hex(LATEST_BLOCK)
    elif method == "eth_getBlockByNumber":
        assert call["params"] == ["latest", False]
        result = _block_result()
    elif method == "eth_getBlockByHash":
        assert call["params"] == [BLOCK_HASH, False]
        result = _block_result()
    elif method == "eth_feeHistory":
        assert call["params"] == [hex(1), "latest", []]
        result = {
            "oldestBlock": hex(LATEST_BLOCK),
            "baseFeePerGas": [hex(BASE_FEE), hex(BASE_FEE + 1)],
            "gasUsedRatio": [0.5],
            "reward": [],
        }
    elif method == "eth_maxPriorityFeePerGas":
        result = hex(PRIORITY_FEE)
    elif method == "eth_getTransactionByHash":
        assert call["params"] == [TX_HASH]
        result = {
            "hash": TX_HASH,
            "blockHash": BLOCK_HASH,
            "blockNumber": hex(LATEST_BLOCK),
            "from": TX_FROM,
            "to": TX_TO,
            "gas": hex(21_000),
            "gasPrice": hex(BASE_FEE + PRIORITY_FEE),
            "nonce": hex(7),
            "transactionIndex": hex(0),
            "value": hex(42),
            "input": "0x",
            "type": hex(2),
            "v": hex(1),
            "r": "0x" + "55" * 32,
            "s": "0x" + "66" * 32,
        }
    else:
        raise AssertionError(f"Unexpected RPC method: {method}")

    return {"jsonrpc": "2.0", "id": call["id"], "result": result}


def _block_result() -> dict[str, object]:
    return {
        "number": hex(LATEST_BLOCK),
        "hash": BLOCK_HASH,
        "parentHash": "0x" + "77" * 32,
        "sha3Uncles": "0x" + "88" * 32,
        "logsBloom": "0x" + "00" * 256,
        "transactionsRoot": "0x" + "99" * 32,
        "stateRoot": "0x" + "aa" * 32,
        "receiptsRoot": "0x" + "bb" * 32,
        "miner": "0x" + "cc" * 20,
        "difficulty": hex(0),
        "totalDifficulty": hex(0),
        "extraData": "0x",
        "size": hex(1024),
        "gasLimit": hex(BLOCK_GAS_LIMIT),
        "gasUsed": hex(15_000_000),
        "timestamp": hex(1_700_000_000),
        "transactions": [TX_HASH],
        "uncles": [],
        "baseFeePerGas": hex(BASE_FEE),
    }
