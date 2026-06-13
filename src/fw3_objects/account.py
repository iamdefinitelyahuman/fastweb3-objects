from __future__ import annotations

from getpass import getpass
from typing import Any
from weakref import WeakSet

import fw3_keypass as kp
from Crypto.Hash import keccak
from fw3.formatters import build_transaction_object, normalize_rpc_obj
from fw3_keypass.crypto.rlp import rlp_encode
from fw3_keypass.db.core import resolve_db_path
from fw3_keypass.utils import checksum_address

from .chain import Chain
from .errors import ChainMismatch, NoActiveChain


class Accounts(kp.KeypassDB):
    """Keypass-backed account database."""

    _instances = WeakSet()

    def __init__(self, name_or_path=None, *, create=None, unlock=True, password=None):
        """Initialize an account database.

        Behavior depends on whether a path is provided and whether the database already
        exists.

        Default behavior:
            - If ``name_or_path`` is ``None``, the default database is used.
            - If the default database does not exist, it is created.
            - If the default database exists, it is opened and unlocked.
            - If ``name_or_path`` is provided and exists, it is opened.
            - If ``name_or_path`` is provided and does not exist, it is created only
              when ``create=True``.

        Args:
            name_or_path: Database name or filesystem path. Uses the default database
                location when omitted.
            create: Whether to create the database if it does not exist. If omitted,
                defaults to ``True`` only for the default database.
            unlock: Whether to unlock an existing database after opening it.
            password: Password used to initialize or unlock the database.

        Raises:
            FileNotFoundError: If the database does not exist and ``create`` is false.
        """
        path = resolve_db_path(name_or_path)
        if create is None:
            create = name_or_path is None
        if path.exists():
            create = False
        elif create is False:
            raise FileNotFoundError("Database does not exist")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(path)
        if create:
            if password is None:
                password = getpass(f"Create password for new Accounts database '{path.stem}': ")
            self.initialize(password)
        elif unlock:
            self.unlock(password)
        self._is_default = resolve_db_path(None) == path
        self._instances.add(self)

    def _make_account(self, address: str) -> Account:
        return Account(address, db=self)

    @classmethod
    def _find_db(cls, address):
        try:
            address = checksum_address(address)
        except (TypeError, ValueError):
            return None

        # first attempt to return an unlocked db
        for db in cls._instances:
            try:
                account = db[address]
            except KeyError:
                continue
            if account.can_sign:
                return db

        # next try for a locked db
        for db in cls._instances:
            try:
                account = db[address]
            except KeyError:
                continue
            if account.has_private_key:
                return db

        return None

    def __repr__(self) -> str:
        state = "unlocked" if self.is_unlocked else "locked"
        name = f"'{self.path.stem}' " if not self._is_default else ""
        return f"<Accounts {name}{state}>"


class Account(kp.Account):
    """Ethereum account object with optional chain binding."""

    def __init__(
        self,
        address: str,
        *,
        db=None,
        chain: Chain | int | None = None,
    ) -> None:
        """Initialize an account wrapper.

        Args:
            address: Account address.
            db: Keypass database that owns the account, if available.
            chain: Chain or chain ID to bind this account to. If omitted, chain-specific
                methods use their ``chain`` argument or the active default chain.
        """
        if db is None:
            db = Accounts._find_db(address)
        super().__init__(address, db=db)
        self._bound_chain = None if chain is None else Chain(chain)

    def on(self, chain: Chain | int) -> Account:
        """Return a copy of this account bound to a chain.

        Args:
            chain: Chain or chain ID to bind.

        Returns:
            Account bound to the requested chain.
        """
        return Account(self.address, db=self._db, chain=chain)

    def balance(
        self,
        *,
        chain: Chain | int | None = None,
        block_identifier: str | int | None = None,
    ) -> int:
        """Return the native token balance for this account.

        Args:
            chain: Chain or chain ID to query. Uses the bound or default chain when
                omitted.
            block_identifier: Optional block number or tag.

        Returns:
            Balance in wei.
        """
        w3 = self._resolve_chain(chain).w3
        if block_identifier is None:
            return w3.eth.get_balance(self.address)
        else:
            return w3.eth.get_balance(self.address, block_identifier)

    def nonce(
        self,
        *,
        chain: Chain | int | None = None,
        block_identifier: str | int | None = None,
    ) -> int:
        """Return the transaction count for this account.

        Args:
            chain: Chain or chain ID to query. Uses the bound or default chain when
                omitted.
            block_identifier: Optional block number or tag.

        Returns:
            Account nonce at the requested block.
        """
        w3 = self._resolve_chain(chain).w3
        if block_identifier is None:
            return w3.eth.get_transaction_count(self.address)
        else:
            return w3.eth.get_transaction_count(self.address, block_identifier)

    def bytecode(
        self,
        *,
        chain: Chain | int | None = None,
        block_identifier: str | int | None = None,
    ):
        """Return code stored at this account address.

        Args:
            chain: Chain or chain ID to query. Uses the bound or default chain when
                omitted.
            block_identifier: Optional block number or tag.

        Returns:
            Contract bytecode, or empty bytes for an externally owned account.
        """
        w3 = self._resolve_chain(chain).w3
        if block_identifier is None:
            return w3.eth.get_code(self.address)
        else:
            return w3.eth.get_code(self.address, block_identifier)

    def storage(
        self,
        position: int | str,
        *,
        chain: Chain | int | None = None,
        block_identifier: str | int | None = None,
    ):
        """Return a storage slot value for this account address.

        Args:
            position: Storage slot index.
            chain: Chain or chain ID to query. Uses the bound or default chain when
                omitted.
            block_identifier: Optional block number or tag.

        Returns:
            Raw storage slot value.
        """
        w3 = self._resolve_chain(chain).w3
        if block_identifier is None:
            return w3.eth.get_storage_at(self.address, position)
        else:
            return w3.eth.get_storage_at(self.address, position, block_identifier)

    def call(
        self,
        to: str | None = None,
        *,
        value: int | str | None = None,
        data: bytes | str | None = None,
        gas_limit: int | str | None = None,
        chain: Chain | int | None = None,
        block_identifier: str | int | None = None,
    ) -> Any:
        """Perform an ``eth_call`` as this account.

        Args:
            to: Destination address.
            value: Call value in wei.
            data: Call data.
            gas_limit: Optional gas limit.
            chain: Chain or chain ID to query. Uses the bound or default chain when
                omitted.
            block_identifier: Optional block number or tag.

        Returns:
            Raw call return data.
        """
        chain = self._resolve_chain(chain)
        if to is not None:
            to = str(to)

        tx_kwargs = dict(
            from_=self.address,
            to=to,
            gas=gas_limit,
            value=value,
            data=data,
            chain_id=int(chain),
            block=block_identifier,
        )
        tx_kwargs = {k: v for k, v in tx_kwargs.items() if v is not None}
        return chain.w3.eth.call(**tx_kwargs)

    def estimate_gas(
        self,
        to: str | None = None,
        value: int | str | None = None,
        *,
        data: bytes | str | None = None,
        chain: Chain | int | None = None,
    ) -> int:
        """Estimate gas for a transaction from this account.

        Args:
            to: Destination address.
            value: Transaction value in wei.
            data: Transaction calldata.
            chain: Chain or chain ID to query. Uses the bound or default chain when
                omitted.

        Returns:
            Estimated gas limit.
        """
        chain = self._resolve_chain(chain)
        if to is not None:
            to = str(to)

        tx_kwargs = dict(from_=self.address, to=to, value=value, data=data, chain_id=int(chain))
        tx_kwargs = {k: v for k, v in tx_kwargs.items() if v is not None}
        return chain.w3.eth.estimate_gas(**tx_kwargs)

    def transact(
        self,
        to: str | None = None,
        value: int | str | None = None,
        *,
        data: bytes | str | None = None,
        gas_limit: int | str | None = None,
        gas_buffer: float | None = None,
        gas_price: int | str | None = None,
        max_fee_per_gas: int | str | None = None,
        max_priority_fee_per_gas: int | str | None = None,
        nonce: int | str | None = None,
        chain: Chain | int | None = None,
    ) -> Any:
        """Sign and broadcast a transaction from this account.

        Missing nonce and fee fields are filled from the target chain before signing.

        Args:
            to: Destination address. May be omitted for contract creation.
            value: Transaction value in wei.
            data: Transaction calldata.
            gas_limit: Explicit gas limit. Estimated when omitted.
            gas_buffer: Multiplier applied to the estimated gas limit.
            gas_price: Legacy gas price.
            max_fee_per_gas: EIP-1559 max fee per gas.
            max_priority_fee_per_gas: EIP-1559 max priority fee per gas.
            nonce: Explicit nonce. Queried when omitted.
            chain: Chain or chain ID to broadcast on. Uses the bound or default chain
                when omitted.

        Returns:
            Transaction object for the broadcast transaction.
        """
        chain = self._resolve_chain(chain)
        if to is not None:
            to = str(to)

        with chain.w3.batch_requests():
            if nonce is None:
                nonce = self.nonce(chain=chain)
            if gas_limit is None:
                gas_limit = self.estimate_gas(to=to, value=value, data=data, chain=chain)
            if gas_price is None:
                if max_priority_fee_per_gas is None:
                    max_priority_fee_per_gas = chain.priority_fee()
                if max_fee_per_gas is None:
                    # query this value last because it flushes the batch queue
                    max_fee_per_gas = int(chain.base_fee() * 1.25)

        if gas_buffer is not None:
            if gas_buffer < 1:
                raise ValueError("Gas buffer must be at least 1")
            gas_limit = int(gas_limit * gas_buffer)

        if gas_price is None:
            if max_priority_fee_per_gas < 100:
                max_priority_fee_per_gas = 100

            if max_priority_fee_per_gas > max_fee_per_gas:
                max_fee_per_gas = max_priority_fee_per_gas

        tx = build_transaction_object(
            from_=self.address,
            to=to,
            gas=gas_limit,
            gas_price=gas_price,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            value=value,
            data=data,
            nonce=nonce,
            chain_id=int(chain),
        )
        raw_tx = self.sign_transaction(tx)
        from .transaction import Transaction

        return Transaction(
            chain.w3.eth.send_raw_transaction(raw_tx.raw_transaction),
            chain=chain,
            allow_unseen=True,
            _txdict=normalize_rpc_obj(tx),
        )

    def get_deployment_address(
        self,
        *,
        nonce: int | None = None,
        chain: Chain | int | None = None,
    ) -> str:
        """Compute this account's CREATE deployment address.

        Args:
            nonce: Nonce to use. If omitted, the current account nonce is queried.
            chain: Chain or chain ID used when ``nonce`` must be queried.

        Returns:
            Checksummed deployment address.

        Raises:
            TypeError: If ``nonce`` is not an integer.
            ValueError: If ``nonce`` is negative.
        """
        if nonce is None:
            nonce = self.nonce(chain=chain)

        if not isinstance(nonce, int):
            raise TypeError("nonce must be an int")
        if nonce < 0:
            raise ValueError("nonce cannot be negative")

        encoded = rlp_encode([bytes.fromhex(self._normalized_address[2:]), nonce])
        digest = keccak.new(digest_bits=256, data=encoded).digest()
        return checksum_address("0x" + digest[-20:].hex())

    def _resolve_chain(
        self,
        chain: Chain | int | None,
    ) -> Chain:
        if self._bound_chain is None:
            if chain is None:
                resolved, _ = Chain._get_default_chain()
                if resolved is None:
                    raise NoActiveChain("No chain specified for unbound Account")
            else:
                resolved = Chain(chain)
        else:
            if chain is not None:
                if self._bound_chain != Chain(chain):
                    raise ChainMismatch(self._bound_chain, chain, "bound Account")
            resolved = self._bound_chain

        return resolved
