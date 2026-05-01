from __future__ import annotations

from enum import IntEnum
from threading import Event

from .account import Account
from .chain import Chain


def tx_property(fn):
    def wrapper(self):
        self._await_initial_update()
        return fn(self)

    return property(wrapper)


class TxStatus(IntEnum):
    CONFIRMED = 1
    REVERTED = 0
    PENDING = -1
    DROPPED = -2
    REPLACED = -3
    UNSEEN = -4


class Transaction:
    def __init__(self, hash, chain, txdict=None):
        self.hash = hash
        self.chain = Chain(chain)

        self._transaction = txdict or {}
        self._receipt = {}
        self._initialized = Event()
        self._status = TxStatus(-4)

        if txdict:
            self._initialized.set()

        # TODO: Register this transaction with the chain transaction monitor.

    def _await_initial_update(self):
        if not self._initialized.is_set():
            self._initialized.wait()

    @tx_property
    def sender(self):
        account = self._transaction.get("from")
        if account is not None:
            account = Account(account, chain=self.chain)
        return account

    @tx_property
    def receiver(self):
        # TODO if receiver is a contract, can we return `Contract` instead?
        account = self._transaction.get("to")
        if account is not None:
            account = Account(account, chain=self.chain)
        return account

    @tx_property
    def value(self):
        return self._transaction.get("value")

    @tx_property
    def nonce(self):
        return self._transaction.get("nonce")

    @tx_property
    def gas(self):
        return self._transaction.get("gas")

    @tx_property
    def gas_price(self):
        return self._transaction.get("gasPrice")

    @tx_property
    def max_fee_per_gas(self):
        return self._transaction.get("maxFeePerGas")

    @tx_property
    def max_priority_fee_per_gas(self):
        return self._transaction.get("maxPriorityFeePerGas")

    @tx_property
    def input(self):
        return self._transaction.get("input")

    @tx_property
    def type(self):
        return self._transaction.get("type")

    @tx_property
    def block_hash(self):
        return self._receipt.get("blockHash") or self._transaction.get("blockHash")

    @tx_property
    def block_number(self):
        return self._receipt.get("blockNumber") or self._transaction.get("blockNumber")

    @tx_property
    def transaction_index(self):
        return self._receipt.get("transactionIndex") or self._transaction.get("transactionIndex")

    @tx_property
    def status(self):
        status = self._receipt.get("status")
        if status is not None:
            return TxStatus(status)
        return self._status

    @tx_property
    def gas_used(self):
        return self._receipt.get("gasUsed")

    @tx_property
    def cumulative_gas_used(self):
        return self._receipt.get("cumulativeGasUsed")

    @tx_property
    def effective_gas_price(self):
        return self._receipt.get("effectiveGasPrice")

    @tx_property
    def contract_address(self):
        return self._receipt.get("contractAddress")

    @tx_property
    def logs(self):
        return self._receipt.get("logs")

    @tx_property
    def logs_bloom(self):
        return self._receipt.get("logsBloom")

    @tx_property
    def events(self):
        raise NotImplementedError

    def confirmations(self):
        # TODO should this be a property?
        raise NotImplementedError

    def wait(self):
        raise NotImplementedError

    def replace(self):
        raise NotImplementedError
