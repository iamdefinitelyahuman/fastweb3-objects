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
    def __init__(
        self,
        address: Account | str,
        abi: list | str | Path | None = None,
        chain: Chain | int | None = None,
        implementation: Account | str | bool | None = None,
        refresh_abi: bool | None = None,
    ):
        """Create a contract bound to an address with optional ABI resolution.

        Calls always execute against ``address``. The ABI used for method dispatch
        may come from multiple sources depending on the inputs:

        - If ``abi`` is provided:
            The ABI is trusted as complete. No explorer lookup or proxy handling
            is performed. If ``refresh_abi`` is ``True``, the ABI is written to
            cache. If ``refresh_abi`` is ``None``, it is cached only if no ABI is
            already stored.

        - If ``implementation`` is an address:
            The ABI is taken from that implementation. If a proxy ABI exists at
            ``address``, it is overlaid on top so proxy selectors take precedence.

        - If ``implementation`` is ``False``:
            Proxy handling is disabled. The ABI is loaded only for ``address``.

        - Otherwise (default):
            The ABI is loaded from cache or fetched from an explorer. If the
            contract is identified as a proxy, the implementation ABI is used and
            overlaid with the proxy ABI.

        Explorer lookups are asynchronous. The constructor returns immediately,
        and the ABI is installed on first access. Until then, the ``abi``
        attribute is not present.

        Args:
            address: Contract address to execute calls against.
            abi: ABI list or path to a JSON ABI file. Bypasses all lookup logic.
            chain: Chain or chain id. Uses the active default chain if omitted.
            implementation: Proxy override. Address = force implementation,
                ``False`` = ignore proxy, ``None`` = auto.
            refresh_abi: Cache control. ``True`` forces refresh, ``False`` uses
                cache only, ``None`` uses cache then falls back to explorer.

        Raises:
            NoActiveChain: If no chain is available.
            FileNotFoundError: If ``abi`` path does not exist.
            TypeError: If ``abi`` is invalid.
            ValueError: If ABI format is invalid.
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
        return abi.function_signature(self.method_abi)

    @property
    def selector(self) -> bytes:
        return abi.function_selector(self.method_abi)

    @property
    def mutability(self) -> str:
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
        gas_limit: int | str | None = None,
        block_identifier: str | int | None = None,
    ):
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
        data = self.encode_input(*args)
        return sender.estimate_gas(to=self.address, value=value, data=data, chain=self.chain)

    def transact(
        self,
        *args,
        sender: Account,
        value: int | str | None = None,
        gas_limit: int | str | None = None,
        gas_buffer: float | None = None,
        gas_price: int | str | None = None,
        max_fee_per_gas: int | str | None = None,
        max_priority_fee_per_gas: int | str | None = None,
        nonce: int | str | None = None,
    ):
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
        return abi.decode_calldata(self.method_abi, hexstr)

    def encode_input(self, *args):
        return abi.encode_calldata(self.method_abi, args)

    def decode_output(self, hexstr: str):
        return abi.decode_returndata(self.method_abi, hexstr)


class OverloadedMethod:
    def __init__(self, address: Account, method_abis: list[dict], chain: Chain):
        self.address = address
        self.chain = chain
        self.method_abis = method_abis

    @property
    def name(self) -> str:
        return self.method_abis[0]["name"]

    @property
    def signatures(self) -> list[str]:
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
        gas_limit: int | str | None = None,
        block_identifier: str | int | None = None,
    ):
        method = self._resolve_by_args(args)
        return method.call(
            *args,
            sender=sender,
            value=value,
            gas_limit=gas_limit,
            block_identifier=block_identifier,
        )

    def estimate_gas(self, *args, sender: Account, value: int | str | None = None):
        method = self._resolve_by_args(args)
        return method.estimate_gas(*args, sender=sender, value=value)

    def transact(
        self,
        *args,
        sender: Account,
        value: int | str | None = None,
        gas_limit: int | str | None = None,
        gas_buffer: float | None = None,
        gas_price: int | str | None = None,
        max_fee_per_gas: int | str | None = None,
        max_priority_fee_per_gas: int | str | None = None,
        nonce: int | str | None = None,
    ):
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
        gas_limit: int | str | None = None,
        gas_buffer: float | None = None,
        gas_price: int | str | None = None,
        max_fee_per_gas: int | str | None = None,
        max_priority_fee_per_gas: int | str | None = None,
        nonce: int | str | None = None,
        block_identifier: str | int | None = None,
    ):
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
    def __call__(
        self,
        *args,
        sender=None,
        value: int | str | None = None,
        gas_limit: int | str | None = None,
        block_identifier: str | int | None = None,
    ):
        return self.call(
            *args,
            sender=sender,
            value=value,
            gas_limit=gas_limit,
            block_identifier=block_identifier,
        )


class ContractTx(_ContractMethod):
    def __call__(
        self,
        *args,
        sender: Account,
        value: int | str | None = None,
        gas_limit: int | str | None = None,
        gas_buffer: float | None = None,
        gas_price: int | str | None = None,
        max_fee_per_gas: int | str | None = None,
        max_priority_fee_per_gas: int | str | None = None,
        nonce: int | str | None = None,
    ):
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
