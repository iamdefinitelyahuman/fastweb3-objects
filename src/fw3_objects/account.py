from __future__ import annotations

from getpass import getpass
from typing import Any

import fw3_keypass as kp
from Crypto.Hash import keccak
from fw3 import Web3
from fw3_keypass.crypto.rlp import rlp_encode
from fw3_keypass.db.core import resolve_db_path
from fw3_keypass.utils import checksum_address

from .chain import Chain
from .errors import ChainMismatch, NoActiveChain


class Accounts(kp.KeypassDB):
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

    def _make_account(self, address: str) -> Account:
        return Account(address, db=self)

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
        w3 = self._resolve_w3(chain)
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
        w3 = self._resolve_w3(chain)
        if block_identifier is None:
            return w3.eth.get_transaction_count(self.address)
        else:
            return w3.eth.get_transaction_count(self.address, block_identifier)

    # --- execution ---

    def call(
        self,
        *,
        to: str | None = None,
        data: bytes | None = None,
        value: int | None = None,
        chain: Chain | int | None = None,
        block_identifier: str | int | None = None,
        **tx_params: Any,
    ) -> Any:
        """
        Perform an eth_call as this account (no state change).
        """
        ...

    def estimate_gas(
        self,
        *,
        to: str | None = None,
        data: bytes | None = None,
        value: int | None = None,
        chain: Chain | int | None = None,
        **tx_params: Any,
    ) -> int:
        """
        Estimate gas for a transaction originating from this account.
        """
        ...

    def transact(
        self,
        *,
        to: str | None = None,
        data: bytes | None = None,
        value: int | None = None,
        chain: Chain | int | None = None,
        **tx_params: Any,
    ) -> Any:
        """
        Sign and send a transaction from this account.
        """
        ...

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

    def _resolve_w3(
        self,
        chain: Chain | int | None,
    ) -> Web3:
        if self._bound_chain is None:
            if chain is None:
                raise NoActiveChain("No chain specified for unbound Account")
            resolved = Chain(chain)
        else:
            if chain is not None:
                if self._bound_chain != Chain(chain):
                    raise ChainMismatch(self._bound_chain, chain, "bound Account")
            resolved = self._bound_chain

        return resolved.w3
