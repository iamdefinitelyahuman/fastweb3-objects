import json
from pathlib import Path

from Crypto.Hash import keccak
from eth.codecs.abi import decode as abi_decode
from eth.codecs.abi import encode as abi_encode
from fw3.deferred import deferred_response

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


def _abi_item_type(item: dict) -> str:
    item_type = item["type"]

    if not item_type.startswith("tuple"):
        return item_type

    suffix = item_type[5:]
    components = item.get("components", [])
    inner = ",".join(_abi_item_type(component) for component in components)
    return f"({inner}){suffix}"


def _abi_schema(items: list[dict]) -> str:
    types = ",".join(_abi_item_type(item) for item in items)
    return f"({types})"


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
        # TODO overloaded methods

        for method_abi in function_abis:
            name = method_abi["name"]
            cls = _method_class(method_abi)
            method = cls(address=self.address, method_abi=method_abi, chain=chain)
            setattr(self, name, method)


class _ContractMethod:
    def __init__(self, address: Account, method_abi: dict, chain: Chain):
        self.address = address
        self.chain = chain
        self.method_abi = method_abi

    @property
    def signature(self) -> str:
        name = self.method_abi["name"]
        inputs = self.method_abi.get("inputs", [])
        types = ",".join(i["type"] for i in inputs)
        return f"{name}({types})"

    @property
    def selector(self) -> bytes:
        k = keccak.new(digest_bits=256)
        k.update(self.signature.encode())
        return k.digest()[:4]

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
        data = bytes.fromhex(hexstr)

        if len(data) < 4:
            raise ValueError("Input data is shorter than a function selector")

        selector = data[:4]
        if selector != self.selector:
            raise ValueError(
                f"Input selector 0x{selector.hex()} does not match "
                "method selector 0x{self.selector.hex()}"
            )

        schema = _abi_schema(self.method_abi.get("inputs", []))
        return abi_decode(schema, data[4:])

    def encode_input(self, *args):
        schema = _abi_schema(self.method_abi.get("inputs", []))
        data = self.selector + abi_encode(schema, args)
        return f"0x{data.hex()}"

    def decode_output(self, hexstr: str):
        schema = _abi_schema(self.method_abi.get("outputs", []))
        values = abi_decode(schema, bytes.fromhex(hexstr))

        if len(values) == 0:
            return None
        if len(values) == 1:
            return values[0]
        return values


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
