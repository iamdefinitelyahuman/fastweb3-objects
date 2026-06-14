import json
import weakref
from dataclasses import dataclass
from pathlib import Path

from fw3.deferred import deferred_response

from . import abi
from .account import Account
from .cache.metadata import AddressMetadataCache
from .chain import Chain
from .errors import ABINotFound, NoActiveChain
from .explorers.lookup import HIGH_PRIORITY, fetch_abi


def _load_abi(abi):
    if isinstance(abi, (str, Path)):
        path = Path(abi)

        if not path.exists():
            raise FileNotFoundError(f"ABI file not found: {path}")

        with path.open() as f:
            data = json.load(f)

        if isinstance(data, list):
            abi_list = data
        elif isinstance(data, dict) and "abi" in data:
            abi_list = data["abi"]
        else:
            raise ValueError("Invalid ABI format: expected list or dict with 'abi' key")

    elif isinstance(abi, list):
        abi_list = abi

    else:
        raise TypeError("abi must be a sequence or a path to a JSON file")

    if not all(isinstance(item, dict) for item in abi_list):
        raise ValueError("ABI must be a sequence of dicts")

    return abi_list


def _method_class(method_abi: dict) -> type["_ContractMethod"]:
    mutability = method_abi.get("stateMutability")

    if mutability is None:
        if method_abi.get("constant", False):
            mutability = "view"
        elif method_abi.get("payable", False):
            mutability = "payable"
        else:
            mutability = "nonpayable"

    if mutability in ("view", "pure"):
        return ContractCall
    return ContractTx


@dataclass
class _ContractState:
    chain: Chain
    abi_job: object | None = None
    proxy_abi: list[dict] | None = None
    implementation: str | None = None
    implementation_contract: object | None = None
    refresh_abi: bool | None = None


_RESERVED_NAMES = {"abi", "address"}
_CONTRACT_STATE = weakref.WeakKeyDictionary()


def _install_abi(contract: "Contract", abi_list: list[dict]) -> None:
    state = _CONTRACT_STATE[contract]

    contract.abi = abi_list

    function_abis = [i for i in contract.abi if i.get("type", "function") == "function"]
    functions = {}

    for method_abi in function_abis:
        name = method_abi["name"]
        if name in _RESERVED_NAMES:
            raise ValueError(f"Contract ABI may not define reserved attribute {name!r}")
        functions.setdefault(name, []).append(method_abi)

    for name, method_abis in functions.items():
        if len(method_abis) == 1:
            method_abi = method_abis[0]
            cls = _method_class(method_abi)
            method = cls(address=contract.address, method_abi=method_abi, chain=state.chain)
        else:
            method = OverloadedMethod(
                address=contract.address, method_abis=method_abis, chain=state.chain
            )

        setattr(contract, name, method)


def _cache_abi_result(cache, chain_id: int, address: str, result) -> None:
    abi_list, implementation = result
    cache.set(chain_id, address, "abi", abi_list)
    if implementation is not None:
        cache.set(chain_id, address, "implementation", implementation)


def _normalize_implementation(implementation, chain: Chain) -> str | None:
    if implementation is None or implementation is False:
        return None
    return str(Account(implementation, chain=chain))


def _start_implementation_lookup(contract: "Contract", refresh_abi: bool | None) -> None:
    state = _CONTRACT_STATE[contract]
    if state.implementation is None or state.implementation_contract is not None:
        return
    state.implementation_contract = Contract(
        state.implementation,
        chain=state.chain,
        refresh_abi=refresh_abi,
    )


def _resolve_abi(contract: "Contract") -> None:
    state = _CONTRACT_STATE[contract]

    if state.proxy_abi is None:
        if state.abi_job is None:
            raise AttributeError("abi")

        state.abi_job.bump_priority(HIGH_PRIORITY)
        try:
            result = state.abi_job.wait()
        except ABINotFound:
            if state.implementation is None:
                raise
            state.proxy_abi = []
        else:
            proxy_abi, implementation = result
            state.proxy_abi = proxy_abi
            if state.implementation is None:
                state.implementation = implementation
        finally:
            state.abi_job = None

        _start_implementation_lookup(contract, state.refresh_abi)

    if state.implementation is None:
        _install_abi(contract, state.proxy_abi)
        return

    _start_implementation_lookup(contract, state.refresh_abi)
    implementation_abi = state.implementation_contract.abi
    _install_abi(contract, abi.overlay_abi(implementation_abi, state.proxy_abi))


class Contract:
    """Contract instance bound to an address and chain."""

    def __init__(
        self,
        address: Account | str,
        abi: list | str | Path | None = None,
        chain: Chain | int | None = None,
        implementation: Account | str | bool | None = None,
        refresh_abi: bool | None = None,
    ):
        """Create a contract bound to an address with optional ABI resolution.

        Calls always execute against ``address``. The ABI used for method dispatch may
        come from multiple sources depending on the inputs:

        - If ``abi`` is provided, it is trusted as complete and no explorer lookup or
          proxy handling is performed.
        - If ``implementation`` is an address, the ABI is taken from that implementation
          and overlaid with any proxy ABI found at ``address``.
        - If ``implementation`` is ``False``, proxy handling is disabled.
        - Otherwise, the ABI is loaded from cache or fetched from an explorer. If the
          contract is identified as a proxy, the implementation ABI is used and overlaid
          with the proxy ABI.

        Explorer lookups are asynchronous. The constructor returns immediately, and the
        ABI is installed on first access. Until then, the ``abi`` attribute may not be
        present.

        Args:
            address: Contract address to execute calls against.
            abi: ABI list or path to a JSON ABI file.
            chain: Chain or chain ID. Uses the active default chain if omitted.
            implementation: Proxy override. Address forces an implementation,
                ``False`` disables proxy handling, and ``None`` enables auto-detection.
            refresh_abi: Cache control. ``True`` forces refresh, ``False`` uses cache
                only, and ``None`` uses cache then falls back to explorer.

        Raises:
            NoActiveChain: If no chain is available.
            FileNotFoundError: If an ABI path does not exist.
            TypeError: If ``abi`` has an unsupported type.
            ValueError: If the ABI format is invalid.
        """
        if chain is None:
            chain, _ = Chain._get_default_chain()
            if chain is None:
                raise NoActiveChain("No chain specified for Contract")

        chain = Chain(chain)
        self.address = Account(address, chain=chain)
        _CONTRACT_STATE[self] = _ContractState(chain=chain, refresh_abi=refresh_abi)

        cache = AddressMetadataCache()

        if abi is not None:
            abi_list = _load_abi(abi)
            _install_abi(self, abi_list)

            if refresh_abi is True:
                cache.set(chain.id, str(self.address), "abi", abi_list)
            elif refresh_abi is None and cache.get(chain.id, str(self.address), "abi") is None:
                cache.set(chain.id, str(self.address), "abi", abi_list)
            return

        state = _CONTRACT_STATE[self]
        forced_implementation = _normalize_implementation(implementation, chain)
        resolve_proxy = implementation is None

        if refresh_abi is not True:
            cached_abi = cache.get(chain.id, str(self.address), "abi")
            cached_implementation = forced_implementation
            if cached_implementation is None and implementation is None:
                cached_implementation = cache.get(chain.id, str(self.address), "implementation")

            if cached_abi is not None:
                if cached_implementation is None:
                    _install_abi(self, _load_abi(cached_abi))
                    return

                state.proxy_abi = _load_abi(cached_abi)
                state.implementation = cached_implementation
                _start_implementation_lookup(self, refresh_abi)
                return

            if refresh_abi is False:
                return

        state.implementation = forced_implementation
        if state.implementation is not None:
            _start_implementation_lookup(self, refresh_abi)

        def on_success(result):
            _cache_abi_result(cache, chain.id, str(self.address), result)
            _, implementation = result
            if state.implementation is None and implementation is not None:
                state.implementation = implementation
                _start_implementation_lookup(self, refresh_abi)

        state.abi_job = fetch_abi(
            chain.id,
            str(self.address),
            ignore_negative_cache=refresh_abi is True,
            resolve_proxy=resolve_proxy,
            on_success=on_success,
        )

    def __str__(self):
        return str(self.address)

    def __getattr__(self, name: str):
        state = _CONTRACT_STATE[self]
        if state.abi_job is None and state.proxy_abi is None:
            raise AttributeError(name)

        _resolve_abi(self)

        try:
            return object.__getattribute__(self, name)
        except AttributeError:
            raise AttributeError(name) from None


class _ContractMethod:
    def __init__(self, address: Account, method_abi: dict, chain: Chain):
        self.address = address
        self.chain = chain
        self.method_abi = method_abi

    @property
    def signature(self) -> str:
        """Return the canonical function signature."""
        return abi.function_signature(self.method_abi)

    @property
    def selector(self) -> bytes:
        """Return the four-byte function selector."""
        return abi.function_selector(self.method_abi)

    @property
    def mutability(self) -> str:
        """Return the function state mutability."""
        if "stateMutability" in self.method_abi:
            return self.method_abi["stateMutability"]

        constant = self.method_abi.get("constant", False)
        payable = self.method_abi.get("payable", False)

        if constant:
            return "view"
        if payable:
            return "payable"
        return "nonpayable"

    def call(
        self,
        *args,
        sender: Account = None,
        value: int | str | None = None,
        gas_limit: int | None = None,
        block_identifier: str | int | None = None,
    ):
        """Execute the function with ``eth_call``.

        Args:
            *args: Contract function arguments.
            sender: Optional account to use as ``msg.sender``. Uses the zero address when
                omitted.
            value: Call value in wei.
            gas_limit: Optional gas limit.
            block_identifier: Optional block number or tag.

        Returns:
            Decoded return value.
        """
        if sender is None:
            sender = Account("0x0000000000000000000000000000000000000000")
        data = self.encode_input(*args)
        resp = sender.call(
            to=str(self.address),
            value=value,
            data=data,
            gas_limit=gas_limit,
            chain=self.chain,
            block_identifier=block_identifier,
        )
        return deferred_response(None, ref_func=lambda h: h.set_value(self.decode_output(resp)))

    def estimate_gas(self, *args, sender: Account, value: int | str | None = None):
        """Estimate gas for this contract function.

        Args:
            *args: Contract function arguments.
            sender: Account sending the transaction.
            value: Transaction value in wei.

        Returns:
            Estimated gas limit.
        """
        data = self.encode_input(*args)
        return sender.estimate_gas(to=self.address, value=value, data=data, chain=self.chain)

    def transact(
        self,
        *args,
        sender: Account,
        value: int | str | None = None,
        gas_limit: int | None = None,
        gas_buffer: float | None = None,
        gas_price: int | str | None = None,
        max_fee_per_gas: int | str | None = None,
        max_priority_fee_per_gas: int | str | None = None,
        nonce: int | str | None = None,
    ):
        """Sign and broadcast a transaction for this contract function.

        Args:
            *args: Contract function arguments.
            sender: Account sending the transaction.
            value: Transaction value in wei.
            gas_limit: Explicit gas limit. Estimated when omitted.
            gas_buffer: Multiplier applied to the estimated gas limit.
            gas_price: Legacy gas price.
            max_fee_per_gas: EIP-1559 max fee per gas.
            max_priority_fee_per_gas: EIP-1559 max priority fee per gas.
            nonce: Explicit nonce. Queried when omitted.

        Returns:
            Transaction object for the broadcast transaction.
        """
        data = self.encode_input(*args)
        return sender.transact(
            to=str(self.address),
            value=value,
            data=data,
            gas_limit=gas_limit,
            gas_buffer=gas_buffer,
            gas_price=gas_price,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            chain=self.chain,
            nonce=nonce,
        )

    def decode_input(self, hexstr: str):
        """Decode calldata for this contract function.

        Args:
            hexstr: Hex-encoded calldata including the function selector.

        Returns:
            Decoded input values.
        """
        return abi.decode_calldata(self.method_abi, hexstr)

    def encode_input(self, *args):
        """Encode calldata for this contract function.

        Args:
            *args: Contract function arguments.

        Returns:
            Hex-encoded calldata including the function selector.
        """
        return abi.encode_calldata(self.method_abi, args)

    def decode_output(self, hexstr: str):
        """Decode return data for this contract function.

        Args:
            hexstr: Hex-encoded return data.

        Returns:
            Decoded return value.
        """
        return abi.decode_returndata(self.method_abi, hexstr)


class OverloadedMethod:
    """Callable wrapper for a contract function with multiple overloads."""

    def __init__(self, address: Account, method_abis: list[dict], chain: Chain):
        self.address = address
        self.chain = chain
        self.method_abis = method_abis

    @property
    def name(self) -> str:
        """Return the overloaded function name."""
        return self.method_abis[0]["name"]

    @property
    def signatures(self) -> list[str]:
        """Return all available overload signatures."""
        return [abi.function_signature(i) for i in self.method_abis]

    def _make_method(self, method_abi: dict) -> _ContractMethod:
        cls = _method_class(method_abi)
        return cls(address=self.address, method_abi=method_abi, chain=self.chain)

    def _input_types(self, method_abi: dict) -> tuple[str, ...]:
        return tuple(i["type"] for i in method_abi.get("inputs", []))

    def _format_available_overloads(self) -> str:
        return "\n".join(self.signatures)

    def _resolve_by_args(self, args: tuple) -> _ContractMethod:
        matches = [i for i in self.method_abis if len(i.get("inputs", [])) == len(args)]

        if len(matches) == 1:
            return self._make_method(matches[0])

        if not matches:
            raise ValueError(
                f"No matching overload for {self.name} with {len(args)} arguments. "
                f"Available overloads:\n{self._format_available_overloads()}"
            )

        raise ValueError(
            f"Ambiguous overload for {self.name} with {len(args)} arguments. "
            f"Available overloads:\n{self._format_available_overloads()}"
        )

    def _normalize_key(self, key) -> tuple[str, ...]:
        if isinstance(key, str):
            if not key:
                return ()
            return tuple(i.strip() for i in key.split(","))
        if isinstance(key, tuple):
            if not all(isinstance(i, str) for i in key):
                raise TypeError("Overload selector tuple must contain only strings")
            return tuple(i.strip() for i in key)
        raise TypeError("Overload selector must be a comma-separated string or tuple of strings")

    def __getitem__(self, key):
        """Select an overload by input type signature.

        Args:
            key: Comma-separated input type string or tuple of input type strings.

        Returns:
            Contract method wrapper for the selected overload.
        """
        input_types = self._normalize_key(key)
        matches = [i for i in self.method_abis if self._input_types(i) == input_types]

        if not matches:
            raise ValueError(
                f"No overload for {self.name} with input types {input_types}. "
                f"Available overloads:\n{self._format_available_overloads()}"
            )

        return self._make_method(matches[0])

    def call(
        self,
        *args,
        sender: Account = None,
        value: int | str | None = None,
        gas_limit: int | None = None,
        block_identifier: str | int | None = None,
    ):
        """Call the overload matching the provided arguments."""
        method = self._resolve_by_args(args)
        return method.call(
            *args,
            sender=sender,
            value=value,
            gas_limit=gas_limit,
            block_identifier=block_identifier,
        )

    def estimate_gas(self, *args, sender: Account, value: int | str | None = None):
        """Estimate gas for the overload matching the provided arguments."""
        method = self._resolve_by_args(args)
        return method.estimate_gas(*args, sender=sender, value=value)

    def transact(
        self,
        *args,
        sender: Account,
        value: int | str | None = None,
        gas_limit: int | None = None,
        gas_buffer: float | None = None,
        gas_price: int | str | None = None,
        max_fee_per_gas: int | str | None = None,
        max_priority_fee_per_gas: int | str | None = None,
        nonce: int | str | None = None,
    ):
        """Broadcast a transaction for the overload matching the provided arguments."""
        method = self._resolve_by_args(args)
        return method.transact(
            *args,
            sender=sender,
            value=value,
            gas_limit=gas_limit,
            gas_buffer=gas_buffer,
            gas_price=gas_price,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            nonce=nonce,
        )

    def __call__(
        self,
        *args,
        sender=None,
        value: int | str | None = None,
        gas_limit: int | None = None,
        gas_buffer: float | None = None,
        gas_price: int | str | None = None,
        max_fee_per_gas: int | str | None = None,
        max_priority_fee_per_gas: int | str | None = None,
        nonce: int | str | None = None,
        block_identifier: str | int | None = None,
    ):
        """Call or transact using the overload matching the provided arguments."""
        method = self._resolve_by_args(args)

        if isinstance(method, ContractCall):
            return method(
                *args,
                sender=sender,
                value=value,
                gas_limit=gas_limit,
                block_identifier=block_identifier,
            )

        return method(
            *args,
            sender=sender,
            value=value,
            gas_limit=gas_limit,
            gas_buffer=gas_buffer,
            gas_price=gas_price,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            nonce=nonce,
        )


class ContractCall(_ContractMethod):
    """Callable wrapper for a view or pure contract function."""

    def __call__(
        self,
        *args,
        sender=None,
        value: int | str | None = None,
        gas_limit: int | None = None,
        block_identifier: str | int | None = None,
    ):
        """Execute the contract function with ``eth_call``."""
        return self.call(
            *args,
            sender=sender,
            value=value,
            gas_limit=gas_limit,
            block_identifier=block_identifier,
        )


class ContractTx(_ContractMethod):
    """Callable wrapper for a nonpayable or payable contract function."""

    def __call__(
        self,
        *args,
        sender: Account,
        value: int | str | None = None,
        gas_limit: int | None = None,
        gas_buffer: float | None = None,
        gas_price: int | str | None = None,
        max_fee_per_gas: int | str | None = None,
        max_priority_fee_per_gas: int | str | None = None,
        nonce: int | str | None = None,
    ):
        """Sign and broadcast a transaction for the contract function."""
        return self.transact(
            *args,
            sender=sender,
            value=value,
            gas_limit=gas_limit,
            gas_buffer=gas_buffer,
            gas_price=gas_price,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            nonce=nonce,
        )
