from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any


class Chain:
    """Canonical chain context.

    Exactly one instance exists per chain ID. The instance owns the active
    `fastweb3.Web3` client used for RPC calls.
    """

    def __new__(cls, chain_id: int) -> "Chain":
        """Return the canonical Chain instance for the given chain ID."""
        ...

    def __init__(self, chain_id: int) -> None:
        """Initialize the chain context (runs once per canonical instance)."""
        ...

    def __repr__(self) -> str:
        """Return a developer-friendly representation."""
        ...

    def __int__(self) -> int:
        """Return the numeric chain ID."""
        ...

    def __len__(self) -> int:
        """Return the current chain height."""
        ...

    def __getitem__(self, block_number: int | slice) -> Any:
        """Return a block or slice of blocks."""
        ...

    @property
    def id(self) -> int:
        """Return the chain ID."""
        ...

    @property
    def w3(self) -> Any:
        """Return the underlying fastweb3.Web3 client."""
        ...

    def height(self) -> int:
        """Return the latest block height."""
        ...

    def block_gas_limit(self) -> int:
        """Return the gas limit of the latest block."""
        ...

    def base_fee(self) -> int:
        """Return the base fee of the latest block."""
        ...

    def priority_fee(self) -> int:
        """Return the suggested priority fee for new transactions."""
        ...

    def get_transaction(self, txid: str | bytes) -> Any:
        """Return a transaction by hash."""
        ...

    def get_block(self, block_identifier: int | str | bytes) -> Any:
        """Return a block by number, hash, or special identifier."""
        ...

    def new_blocks(
        self,
        height_buffer: int = 0,
        poll_interval: float = 5.0,
    ) -> Iterator[Any]:
        """Yield new blocks as they are observed."""
        ...

    def as_default(self, *, strict: bool = False) -> AbstractContextManager["Chain"]:
        """Context manager that temporarily sets this chain as the default."""
        ...

    # --- internal lifecycle hooks ---

    def _create_w3(self, **w3_params: Any) -> None:
        """Instantiate and assign a new fastweb3.Web3 client."""
        ...
