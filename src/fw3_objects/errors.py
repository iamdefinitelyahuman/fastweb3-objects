from __future__ import annotations


class FW3ObjectsError(Exception):
    pass


class ChainMismatch(FW3ObjectsError):
    pass


class NoActiveChain(FW3ObjectsError):
    pass
