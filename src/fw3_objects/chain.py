from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any

from fw3 import Web3
from fw3.deferred import deferred_response
from fw3.validation import block_ref, hash32

from .errors import ChainMismatch


class _DefaultChainContext(AbstractContextManager["Chain"]):
    """Context manager for temporarily setting the thread-local default chain."""

    def __init__(self, chain: "Chain", *, strict: bool = False) -> None:
        self._chain = chain
        self._strict = strict
        self._previous: tuple[Chain | None, bool] = (None, False)

    def __enter__(self) -> "Chain":
        self._previous = Chain._get_default_chain()
        previous_chain, previous_strict = self._previous

        if previous_strict:
            raise ChainMismatch(
                previous_chain, self._chain, "cannot nest default Chain contexts inside strict mode"
            )

        if self._strict and previous_chain is not None and previous_chain is not self._chain:
            raise ChainMismatch(
                previous_chain, self._chain, "default Chain context manager in strict mode"
            )

        Chain._set_default_chain(self._chain, self._strict)
        return self._chain

    def __exit__(self, exc_type, exc, tb) -> None:
        Chain._set_default_chain(*self._previous)
        return None


class Chain:
    """Canonical chain context for block and transaction access."""

    _instances: dict[int, "Chain"] = {}
    _instances_lock = threading.RLock()

    _thread_local = threading.local()

    def __new__(cls, chain_id: int) -> "Chain":
        """Return the canonical instance for ``chain_id``.

        Args:
            chain_id: Chain ID to instantiate.

        Returns:
            The canonical ``Chain`` instance for the given chain ID.
        """
        chain_id = int(chain_id)

        with cls._instances_lock:
            if chain_id not in cls._instances:
                cls._instances[chain_id] = super().__new__(cls)

            return cls._instances[chain_id]

    def __init__(self, chain_id: int) -> None:
        """Initialize the chain and create its default Web3 client.

        Args:
            chain_id: Chain ID to initialize.
        """
        if getattr(self, "_initialized", False):
            return

        self._initialized = True
        self._chain_id = int(chain_id)
        self._w3_params: dict[str, Any] = {}
        self._w3: Web3 | None = None

    def __repr__(self) -> str:
        """Return a developer-friendly representation."""
        return f"Chain({self.id})"

    def __int__(self) -> int:
        """Return the numeric chain ID."""
        return self.id

    def __len__(self) -> int:
        """Return the number of addressable block indices."""
        height = self.height()
        return deferred_response(None, ref_func=lambda h: h.set_value(height + 1))

    def __getitem__(self, block_number: int):
        """Return a block by number.

        Negative indices are resolved relative to the latest block.

        Args:
            block_number: Absolute or negative-relative block number.

        Returns:
            The requested block object.

        Raises:
            TypeError: If ``block_number`` is not an integer.
            IndexError: If a negative index resolves before genesis.

        Notes:
            Negative indices (``-2`` and below) require an additional RPC call to
            resolve the latest block height. As a result, accessing the returned
            value will perform I/O on first use.
        """

        if isinstance(block_number, slice):
            raise TypeError("Slicing is not supported")

        if not isinstance(block_number, int):
            raise TypeError("block_number must be int")

        if block_number > -2:
            if block_number == -1:
                block_number = "latest"
            return self.get_block(block_number)

        height = self.height()

        def ref_func(handle):
            blk = height + 1 + block_number
            if blk < 0:
                raise IndexError("block index out of range")
            handle.set_value(self.get_block(blk))

        return deferred_response(None, ref_func=ref_func)

    @property
    def id(self) -> int:
        """Return the chain ID."""
        return self._chain_id

    @property
    def w3(self) -> Web3:
        """
        Return the configured ``Web3`` instance for this chain.

        The instance is created lazily on first access.

        When a default chain context is active in strict mode, access is only
        permitted on that chain. Attempting to access ``w3`` on any other chain
        will raise.

        Raises:
            ChainMismatch: If a strict default chain context is active and this
                chain is not the default.
        """
        default_chain, strict = self._get_default_chain()
        if strict and default_chain is not None and default_chain is not self:
            raise ChainMismatch(default_chain, self, "strict default chain")
        if self._w3 is None:
            self._create_w3(**self._w3_params)
        return self._w3

    def height(self) -> int:
        """Return the latest block number."""
        return self.w3.eth.block_number()

    def block_gas_limit(self) -> int:
        """Return the gas limit of the latest block."""
        block = self.get_block("latest")

        return deferred_response(None, ref_func=lambda h: h.set_value(block["gasLimit"]))

    def base_fee(self) -> int:
        """Return the base fee per gas of the latest block."""
        fee_history = self.w3.eth.fee_history(1, "latest", [])
        return deferred_response(
            None, ref_func=lambda h: h.set_value(fee_history["baseFeePerGas"][0])
        )

    def priority_fee(self) -> int:
        """Return the suggested max priority fee per gas."""
        return self.w3.eth.max_priority_fee_per_gas()

    def get_transaction(self, txid: str | bytes):
        """Return a transaction by hash.

        Args:
            txid: Transaction hash as hex string or bytes.

        Returns:
            The requested transaction object.
        """
        # TODO this will eventually return a `Transaction` object from this library
        return self.w3.eth.get_transaction_by_hash(txid)

    def get_block(self, block_identifier: int | str | bytes):
        """Return a block by number, tag, or hash.

        Args:
            block_identifier: Block number, block tag, or block hash.

        Returns:
            The requested block object.
        """
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
        """Yield new blocks as they become available.

        Args:
            height_buffer: Number of blocks behind the tip to follow.
            poll_interval: Target polling interval in seconds.

        Yields:
            Each new buffered block, in ascending height order.

        Raises:
            ValueError: If ``height_buffer`` is negative.
            ValueError: If ``poll_interval`` is not positive.
        """
        if height_buffer < 0:
            raise ValueError("height_buffer must be >= 0")

        if poll_interval <= 0:
            raise ValueError("poll_interval must be > 0")

        last_height = max(0, self.height() - height_buffer)

        while True:
            started_at = time.monotonic()
            current_height = max(0, self.height() - height_buffer)

            while current_height > last_height:
                last_height += 1
                yield self.get_block(last_height)

            elapsed = time.monotonic() - started_at
            time.sleep(max(0, poll_interval - elapsed))

    def as_default(self, *, strict: bool = False) -> AbstractContextManager["Chain"]:
        """Return a context manager that sets this thread's default chain.

        Args:
            strict: Whether to raise if a different default chain is already set.

        Returns:
            A context manager that restores the previous default chain on exit.
        """
        return _DefaultChainContext(self, strict=strict)

    def _create_w3(self, **w3_params: Any) -> None:
        """Create and assign a new ``Web3`` instance for this chain."""
        self._w3_params = dict(w3_params)
        self._w3 = Web3(chain_id=self.id, **self._w3_params)

    @classmethod
    def _get_default_chain(cls) -> tuple["Chain | None", bool]:
        return (
            getattr(cls._thread_local, "default_chain", None),
            getattr(cls._thread_local, "default_chain_strict", False),
        )

    @classmethod
    def _set_default_chain(cls, chain: "Chain | None", strict: bool) -> None:
        cls._thread_local.default_chain = chain
        cls._thread_local.default_chain_strict = strict


def configure_chain(chain: Chain | int, **w3_params: Any) -> None:
    """Configure the canonical ``Chain`` instance for a chain ID.

    Args:
        chain: Chain instance or chain ID to configure.
        **w3_params: Keyword arguments forwarded to ``Web3``.
    """
    Chain(chain)._create_w3(**w3_params)
