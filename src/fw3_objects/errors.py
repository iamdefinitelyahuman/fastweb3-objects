from __future__ import annotations


class FW3ObjectsError(Exception):
    pass


class ABITypeError(TypeError):
    pass


class ABIValueError(ValueError):
    pass


class ABINotFound(FW3ObjectsError):
    pass


class ExplorerError(FW3ObjectsError):
    pass


class ExplorerConnectionError(ExplorerError):
    pass


class ExplorerRateLimited(ExplorerError):
    def __init__(self, provider: str, retry_after: float | None = None):
        self.provider = provider
        self.retry_after = retry_after
        msg = f"{provider} rate limit exceeded"
        if retry_after is not None:
            msg = f"{msg}; retry after {retry_after:g}s"
        super().__init__(msg)


class ChainMismatch(FW3ObjectsError):
    def __init__(self, active_chain, target_chain, context: str | None = None):
        self.active_chain_id = int(active_chain)
        self.target_chain_id = int(target_chain)
        msg = f"Active chain is {self.active_chain_id}, got {self.target_chain_id}"
        if context:
            msg = f"{msg} {context})"
        super().__init__(msg)


class NoActiveChain(FW3ObjectsError):
    pass
