from __future__ import annotations


class FW3ObjectsError(Exception):
    pass


class ABITypeError(TypeError):
    pass


class ABIValueError(ValueError):
    pass


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
