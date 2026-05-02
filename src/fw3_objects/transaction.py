from __future__ import annotations

import time
from enum import IntEnum
from threading import Event

from .account import Account, Accounts
from .chain import Chain
from .errors import NoActiveChain, TransactionNotFound


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
    def __init__(
        self,
        hash: str,
        chain: Chain | int | None = None,
        *,
        allow_unseen: bool = False,
        _txdict: dict | None = None,
    ):
        if not isinstance(hash, str):
            raise TypeError("Transaction hash must be a string")

        if len(hash) != 66 or not hash.startswith("0x"):
            raise ValueError("Invalid transaction hash")

        try:
            int(hash[2:], 16)
        except ValueError:
            raise ValueError("Invalid transaction hash") from None

        if chain is None:
            chain, _ = Chain._get_default_chain()
            if chain is None:
                raise NoActiveChain("No chain specified for Transaction")

        self.hash = hash.lower()
        self.chain = Chain(chain)

        self._transaction = _txdict or {}
        self._receipt = {}
        self._initialized = Event()
        self._finalized = Event()
        self._status = TxStatus.UNSEEN
        self._allow_unseen = allow_unseen or bool(_txdict)

        if _txdict:
            self._initialized.set()

        self.chain._transaction_monitor.watch(self)

    def _await_initial_update(self):
        if not self._initialized.is_set():
            self._initialized.wait()
        if self._status == TxStatus.UNSEEN and not self._allow_unseen:
            raise TransactionNotFound(self.hash)

    @tx_property
    def sender(self):
        account = self._transaction.get("from")
        if account is not None:
            account = Accounts._find_signer(account) or Account(account, chain=self.chain)
        return account

    @tx_property
    def receiver(self):
        # TODO if receiver is a contract, can we return `Contract` instead?
        account = self._transaction.get("to")
        if account is not None:
            account = Accounts._find_signer(account) or Account(account, chain=self.chain)
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
        block_number = self.block_number
        if block_number is None:
            return 0
        return max(0, self.chain.height() - block_number + 1)

    def wait(self, required_confs=1):
        if required_confs < 1:
            return
        self._finalized.wait()
        if required_confs > 1:
            while self.confirmations() < required_confs:
                time.sleep(1)

    def replace(self, increment=1.125):
        if self._finalized.is_set():
            raise ValueError(f"Cannot replace transaction with status {self.status.name}")

        sender = self.sender
        if not sender.has_private_key:
            raise ValueError("Cannot replace transaction because no signer was found for sender")

        kwargs = {
            "to": self.receiver,
            "value": self.value,
            "data": self.input,
            "gas_limit": self.gas,
            "nonce": self.nonce,
            "chain": self.chain,
        }

        if self.gas_price is not None:
            kwargs["gas_price"] = _bump_fee(self.gas_price, increment)
        else:
            kwargs["max_fee_per_gas"] = _bump_fee(self.max_fee_per_gas, increment)
            kwargs["max_priority_fee_per_gas"] = _bump_fee(self.max_priority_fee_per_gas, increment)

        return sender.transact(**kwargs)


def _bump_fee(original, increment):
    return max(int(original * increment), original + 1)
