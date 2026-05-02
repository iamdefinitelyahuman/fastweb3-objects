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
    _instances = WeakSet()

    def __init__(self, name_or_path=None, *, create=None, unlock=True, password=None):
        """
        Initialize an Accounts database.

        This constructor provides a streamlined interface for opening or creating
        a keypass-backed account database. Behavior depends on whether a path is
        provided and whether the database already exists.

        Default behavior:
        - If `name_or_path` is None, the default database is used.
        - If it does not exist, it will be created.
        - If it exists, it will be opened and unlocked.
        - If `name_or_path` is provided:
        - If the database exists, it will be opened.
        - If it does not exist, it will only be created if `create=True`,
            otherwise a FileNotFoundError is raised.

        Args:
            name_or_path (str | os.PathLike | None):
                Name or filesystem path of the database. If None, the default
                database location is used.

            create (bool | None):
                Whether to create the database if it does not exist.
                - If None, defaults to True when using the default database,
                and False otherwise.
                - If False and the database does not exist, raises FileNotFoundError.

            unlock (bool):
                Whether to unlock the database after opening. Ignored when creating
                a new database.

            password (str | None):
                Password used to initialize or unlock the database.
                - If creating a new database and None, the user is prompted.
                - If unlocking an existing database and None, the underlying
                keypass logic may prompt for input.

        Raises:
            FileNotFoundError:
                If the database does not exist and `create` is False.
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
    def _find_signer(cls, address):
        for accounts in cls._instances:
            try:
                return accounts[address]
            except KeyError:
                continue
        return None

    def __repr__(self) -> str:
        state = "unlocked" if self.is_unlocked else "locked"
        name = f"'{self.path.stem}' " if not self._is_default else ""
        return f"<Accounts {name}{state}>"


class Account(kp.Account):
    def __init__(
        self,
        address: str,
        *,
        db=None,
        chain: Chain | int | None = None,
    ) -> None:
        super().__init__(address, db=db)
        self._bound_chain = None if chain is None else Chain(chain)

    def on(self, chain: Chain | int) -> Account:
        """
        Return a new Account instance bound to the given chain.
        """
        return Account(self.address, db=self._db, chain=chain)

    def balance(
        self,
        *,
        chain: Chain | int | None = None,
        block_identifier: str | int | None = None,
    ) -> int:
        """
        Return native token balance for this account.
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
        """
        Return the transaction nonce for this account.
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
        """
        Perform an eth_call as this account without broadcasting a transaction.
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
        """
        Estimate gas for a transaction originating from this account.
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
        """
        Sign and broadcast a transaction from this account.
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
        """
        Compute the CREATE address for this account at the given nonce.
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
