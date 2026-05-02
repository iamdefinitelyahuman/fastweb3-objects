from __future__ import annotations

import time
from threading import Event, RLock, Thread
from weakref import WeakSet

from .transaction import TxStatus

MAX_BATCH_SIZE = 100
POLL_INTERVAL = 1.0


class TransactionMonitor:
    def __init__(self, chain):
        self.chain = chain
        self._watched = WeakSet()
        self._lock = RLock()
        self._wake = Event()
        self._thread = None
        self.last_error = None

    def watch(self, tx):
        with self._lock:
            self._watched.add(tx)
            if self._thread is None:
                self._thread = Thread(target=self._run, daemon=True)
                self._thread.start()
        self._wake.set()

    def _run(self):
        while True:
            started_at = time.monotonic()
            self._poll_once()
            elapsed = time.monotonic() - started_at
            self._wake.wait(max(0, POLL_INTERVAL - elapsed))
            self._wake.clear()

    def _poll_once(self):
        with self._lock:
            watched = tuple(self._watched)

        if not watched:
            return

        transactions_per_batch = max(1, MAX_BATCH_SIZE // 3)
        for batch in _chunks(watched, transactions_per_batch):
            try:
                self._poll_batch(batch)
            except Exception as exc:
                self.last_error = exc

    def _poll_batch(self, watched):
        w3 = self.chain.w3
        known = [tx for tx in watched if tx._transaction.get("from")]
        senders = {tx._transaction["from"] for tx in known}

        with w3.batch_requests():
            tx_data = {tx: w3.eth.get_transaction_by_hash(tx.hash) for tx in watched}
            receipts = {tx: w3.eth.get_transaction_receipt(tx.hash) for tx in watched}
            latest_nonces = {
                sender: w3.eth.get_transaction_count(sender, "latest") for sender in senders
            }

        for tx in watched:
            txdict = tx_data[tx]
            receipt = receipts[tx]

            if bool(receipt):
                # receipt is available, transaction has confirmed.
                if bool(txdict):
                    tx._transaction = txdict
                tx._receipt = receipt
                tx._status = TxStatus(receipt["status"])
                tx._finalized.set()

            elif bool(txdict):
                # receipt not available, but transaction is. the transaction
                # is currently sitting in a the public mempool.
                tx._transaction = txdict
                tx._status = TxStatus.PENDING

            else:
                # receipt and transaction are both unavailable from node
                sender = tx._transaction.get("from")
                if sender and latest_nonces.get(sender, 0) > tx._transaction["nonce"]:
                    # we know the sender and nonce, because the transaction was either
                    # previously seen publicly or seeded locally. the sender's nonce has
                    # advanced beyond the nonce of this transaction, but the transaction
                    # did not confirm. finalize as replaced by another transaction.
                    tx._status = TxStatus.REPLACED
                    tx._finalized.set()
                elif tx._status != TxStatus.UNSEEN:
                    # transaction was previously seen, but we cannot say for sure
                    # that it was replaced - only that it is no longer available from
                    # the node. mark it as dropped.
                    tx._status = TxStatus.DROPPED
                elif not tx._allow_unseen:
                    # transaction was never seen and caller did not ask us to keep
                    # watching unseen hashes. finalize as not found.
                    tx._finalized.set()

            tx._initialized.set()

        remove = {i for i in watched if i._finalized.is_set()}

        if remove:
            with self._lock:
                self._watched.difference_update(remove)


def _chunks(values, size):
    values = tuple(values)
    for idx in range(0, len(values), size):
        yield values[idx : idx + size]
