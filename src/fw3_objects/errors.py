from __future__ import annotations


class FW3ObjectsError(Exception):
    """Base exception for fw3-objects errors."""

    pass


class ABITypeError(TypeError):
    """Raised when a Python value has the wrong type for ABI encoding."""

    pass


class ABIValueError(ValueError):
    """Raised when a Python value is outside ABI bounds or malformed."""

    pass


class ABINotFound(FW3ObjectsError):
    """Raised when no ABI is available for a contract address."""

    pass


class ExplorerError(FW3ObjectsError):
    """Base exception for block explorer lookup failures."""

    pass


class ExplorerConnectionError(ExplorerError):
    """Raised when an explorer request cannot be completed."""

    pass


class ExplorerRateLimited(ExplorerError):
    """Raised when an explorer provider rate-limits ABI lookup."""

    def __init__(self, provider: str, retry_after: float | None = None):
        """Initialize an explorer rate-limit error.

        Args:
            provider: Explorer provider name.
            retry_after: Optional retry delay in seconds.
        """
        self.provider = provider
        self.retry_after = retry_after
        msg = f"{provider} rate limit exceeded"
        if retry_after is not None:
            msg = f"{msg}; retry after {retry_after:g}s"
        super().__init__(msg)


class ChainMismatch(FW3ObjectsError):
    """Raised when an object is used with an incompatible chain."""

    def __init__(self, active_chain, target_chain, context: str | None = None):
        """Initialize a chain mismatch error.

        Args:
            active_chain: Currently active or bound chain.
            target_chain: Chain that was requested.
            context: Optional context included in the error message.
        """
        self.active_chain_id = int(active_chain)
        self.target_chain_id = int(target_chain)
        msg = f"Active chain is {self.active_chain_id}, got {self.target_chain_id}"
        if context:
            msg = f"{msg} {context})"
        super().__init__(msg)


class NoActiveChain(FW3ObjectsError):
    """Raised when an operation requires a chain but none was supplied."""

    pass


class TransactionNotFound(FW3ObjectsError):
    """Raised when a transaction hash cannot be found."""

    def __init__(self, hash):
        """Initialize a transaction-not-found error.

        Args:
            hash: Missing transaction hash.
        """
        super().__init__(f"Transaction not found: {hash}")
