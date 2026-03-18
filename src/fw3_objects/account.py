from __future__ import annotations

from typing import Any, Optional

import fw3_keypass as kp

from .chain import Chain


class Account(kp.Account):
    def __init__(
        self,
        address: str,
        *,
        chain: Chain | int | None = None,
    ) -> None:
        self.address: str
        self._bound_chain: Optional[Chain]

    # --- binding ---

    def on(self, chain: Chain | int) -> Account:
        """
        Return a new Account instance bound to the given chain.
        """
        ...

    # --- core state ---

    def balance(
        self,
        *,
        chain: Chain | int | None = None,
        block_identifier: str | int | None = None,
    ) -> int:
        """
        Return native token balance for this account.
        """
        ...

    def nonce(
        self,
        *,
        chain: Chain | int | None = None,
        block_identifier: str | int | None = None,
    ) -> int:
        """
        Return the transaction nonce for this account.
        """
        ...

    # --- execution ---

    def call(
        self,
        *,
        to: str | None = None,
        data: bytes | None = None,
        value: int | None = None,
        chain: Chain | int | None = None,
        block_identifier: str | int | None = None,
        **tx_params: Any,
    ) -> Any:
        """
        Perform an eth_call as this account (no state change).
        """
        ...

    def estimate_gas(
        self,
        *,
        to: str | None = None,
        data: bytes | None = None,
        value: int | None = None,
        chain: Chain | int | None = None,
        **tx_params: Any,
    ) -> int:
        """
        Estimate gas for a transaction originating from this account.
        """
        ...

    def transact(
        self,
        *,
        to: str | None = None,
        data: bytes | None = None,
        value: int | None = None,
        chain: Chain | int | None = None,
        **tx_params: Any,
    ) -> Any:
        """
        Sign and send a transaction from this account.
        """
        ...

    # --- signing ---

    def sign(
        self,
        payload: bytes | dict[str, Any],
        *,
        chain: Chain | int | None = None,
    ) -> Any:
        """
        Sign arbitrary data or a transaction payload.
        """
        ...

    # --- deployment utilities ---

    def get_deployment_address(
        self,
        *,
        nonce: int | None = None,
        chain: Chain | int | None = None,
    ) -> str:
        """
        Compute the CREATE address for this account at the given nonce.
        """
        ...

    # --- internal helpers ---

    def _resolve_chain(
        self,
        chain: Chain | int | None,
    ) -> Chain:
        """
        Resolve and validate the effective chain for this operation.
        """
        ...
