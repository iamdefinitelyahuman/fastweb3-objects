import json
from pathlib import Path

from fw3.deferred import deferred_response

from . import abi
from .account import Account
from .chain import Chain
from .errors import NoActiveChain


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


class Contract:
    def __init__(
        self, address: Account | str, abi: list | str | Path, chain: Chain | int | None = None
    ):
        if chain is None:
            chain = Chain._get_default_chain()
            if chain is None:
                raise NoActiveChain("No chain specified for Contract")

        self.address = Account(address, chain=chain)
        self.abi = _load_abi(abi)

        function_abis = [i for i in self.abi if i.get("type", "function") == "function"]
        functions = {}

        for method_abi in function_abis:
            name = method_abi["name"]
            functions.setdefault(name, []).append(method_abi)

        for name, method_abis in functions.items():
            if len(method_abis) == 1:
                method_abi = method_abis[0]
                cls = _method_class(method_abi)
                method = cls(address=self.address, method_abi=method_abi, chain=chain)
            else:
                method = OverloadedMethod(
                    address=self.address, method_abis=method_abis, chain=chain
                )

            setattr(self, name, method)


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
        # TODO: argument coercion + validation
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
