from __future__ import annotations

import time
from threading import Event, RLock, Thread

from .transaction import TxStatus

MAX_BATCH_SIZE = 100
POLL_INTERVAL = 1.0


class TransactionMonitor:
    def __init__(self, chain):
        self.chain = chain
        self._watched = set()
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
        known = [tx for tx in watched if _has_sender_and_nonce(tx)]
        senders = {tx._transaction["from"] for tx in known}

        with w3.batch_requests():
            tx_data = {tx: w3.eth.get_transaction_by_hash(tx.hash) for tx in watched}
            receipts = {tx: w3.eth.get_transaction_receipt(tx.hash) for tx in watched}
            latest_nonces = {
                sender: w3.eth.get_transaction_count(sender, "latest") for sender in senders
            }

        remove = set()

        for tx in watched:
            txdict = tx_data[tx]
            receipt = receipts[tx]
            tx._error = None

            if receipt is not None:
                if txdict is not None:
                    tx._transaction = txdict
                tx._receipt = receipt
                tx._initialized.set()
                remove.add(tx)
                continue

            if txdict is not None:
                tx._transaction = txdict
                tx._status = TxStatus.PENDING
                tx._initialized.set()
                continue

            if tx._status == TxStatus.UNSEEN and not tx._allow_unseen:
                tx._initialized.set()
                remove.add(tx)
                continue

            if _has_sender_and_nonce(tx):
                sender = tx._transaction["from"]
                nonce = tx._transaction["nonce"]
                if latest_nonces.get(sender, 0) > nonce:
                    tx._status = TxStatus.REPLACED
                    tx._initialized.set()
                    remove.add(tx)
                    continue

            if tx._status != TxStatus.UNSEEN:
                tx._status = TxStatus.DROPPED

            tx._initialized.set()

        if remove:
            with self._lock:
                self._watched.difference_update(remove)


def _has_sender_and_nonce(tx):
    return "from" in tx._transaction and "nonce" in tx._transaction


def _chunks(values, size):
    values = tuple(values)
    for idx in range(0, len(values), size):
        yield values[idx : idx + size]
