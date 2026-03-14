from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any

from fw3 import Web3
from fw3.validation import block_ref, hash32

from .errors import ChainMismatch


class _DefaultChainContext(AbstractContextManager["Chain"]):
    def __init__(self, chain: "Chain", *, strict: bool = False) -> None:
        self._chain = chain
        self._strict = strict
        self._previous: Chain | None = None

    def __enter__(self) -> "Chain":
        self._previous = Chain._get_default_chain()

        if self._strict and self._previous is not None and self._previous is not self._chain:
            raise ChainMismatch(f"default chain already set to {self._previous!r}")

        Chain._set_default_chain(self._chain)
        return self._chain

    def __exit__(self, exc_type, exc, tb) -> None:
        Chain._set_default_chain(self._previous)
        return None


class Chain:
    _instances: dict[int, "Chain"] = {}
    _instances_lock = threading.RLock()

    _thread_local = threading.local()

    def __new__(cls, chain_id: int) -> "Chain":
        chain_id = int(chain_id)

        with cls._instances_lock:
            if chain_id not in cls._instances:
                cls._instances[chain_id] = super().__new__(cls)

            return cls._instances[chain_id]

    def __init__(self, chain_id: int) -> None:
        if getattr(self, "_initialized", False):
            return

        self._initialized = True
        self._chain_id = int(chain_id)
        self._w3_params: dict[str, Any] = {}
        self._w3: Web3 | None = None

        self._create_w3()

    def __repr__(self) -> str:
        return f"Chain({self.id})"

    def __int__(self) -> int:
        return self.id

    def __len__(self) -> int:
        return self.height() + 1

    def __getitem__(self, block_number: int):

        if isinstance(block_number, slice):
            raise TypeError("Slicing is not supported")

        if not isinstance(block_number, int):
            raise TypeError("block_number must be int")

        if block_number < 0:
            block_number = self.height() + 1 + block_number
            if block_number < 0:
                raise IndexError("block index out of range")

        return self.get_block(block_number)

    @property
    def id(self) -> int:
        return self._chain_id

    @property
    def w3(self) -> Web3:
        assert self._w3 is not None
        return self._w3

    def height(self) -> int:
        return self.w3.eth.block_number()

    def block_gas_limit(self) -> int:
        block = self[-1]

        return block["gasLimit"]

    def base_fee(self) -> int:
        return self.w3.eth.fee_history(1, "latest", [])["baseFeePerGas"][0]

    def priority_fee(self) -> int:
        return self.w3.eth.max_priority_fee_per_gas()

    def get_transaction(self, txid: str | bytes):
        return self.w3.eth.get_transaction(txid)

    def get_block(self, block_identifier: int | str | bytes):
        if isinstance(block_identifier, bytes):
            return self.w3.eth.get_block_by_hash(
                hash32(block_identifier, name="block", strict=True)
            )

        normalized = block_ref(block_identifier, strict=True)

        if (
            isinstance(block_identifier, str)
            and normalized.startswith("0x")
            and len(normalized) == 66
        ):
            return self.w3.eth.get_block_by_hash(normalized)

        return self.w3.eth.get_block_by_number(normalized)

    def new_blocks(
        self,
        height_buffer: int = 0,
        poll_interval: float = 5.0,
    ) -> Iterator:
        if height_buffer < 0:
            raise ValueError("height_buffer must be >= 0")

        if poll_interval <= 0:
            raise ValueError("poll_interval must be > 0")

        last_height = max(0, self.height() - height_buffer)

        while True:
            started_at = time.monotonic()
            current_height = max(0, self.height() - height_buffer)

            if current_height != last_height:
                last_height = current_height
                yield self.get_block(current_height)

            elapsed = time.monotonic() - started_at
            time.sleep(max(0, poll_interval - elapsed))

    def as_default(self, *, strict: bool = False) -> AbstractContextManager["Chain"]:
        return _DefaultChainContext(self, strict=strict)

    def _create_w3(self, **w3_params: Any) -> None:
        self._w3_params = dict(w3_params)
        self._w3 = Web3(chain_id=self.id, **self._w3_params)

    @classmethod
    def _get_default_chain(cls) -> "Chain | None":
        return getattr(cls._thread_local, "default_chain", None)

    @classmethod
    def _set_default_chain(cls, chain: "Chain | None") -> None:
        cls._thread_local.default_chain = chain


def configure_chain(chain: Chain | int, **w3_params: Any) -> None:
    Chain(chain)._create_w3(**w3_params)
