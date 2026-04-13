from Crypto.Hash import keccak
from eth.codecs import abi as _abi


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


def encode(schema: str, values: tuple) -> bytes:
    return _abi.encode(schema, values)


def decode(schema: str, data: bytes):
    return _abi.decode(schema, data)


def function_signature(method_abi: dict) -> str:
    name = method_abi["name"]
    inputs = method_abi.get("inputs", [])
    types = ",".join(i["type"] for i in inputs)
    return f"{name}({types})"


def function_selector(method_abi: dict) -> bytes:
    k = keccak.new(digest_bits=256)
    k.update(function_signature(method_abi).encode())
    return k.digest()[:4]


def decode_calldata(method_abi: dict, hexstr: str):
    data = bytes.fromhex(hexstr.removeprefix("0x"))

    if len(data) < 4:
        raise ValueError("Input data is shorter than a function selector")

    selector = data[:4]
    expected_selector = function_selector(method_abi)
    if selector != expected_selector:
        raise ValueError(
            f"Input selector 0x{selector.hex()} does not match "
            f"method selector 0x{expected_selector.hex()}"
        )

    schema = _abi_schema(method_abi.get("inputs", []))
    return decode(schema, data[4:])


def encode_calldata(method_abi: dict, args: tuple):
    schema = _abi_schema(method_abi.get("inputs", []))
    data = function_selector(method_abi) + encode(schema, args)
    return f"0x{data.hex()}"


def decode_returndata(method_abi: dict, hexstr: str):
    schema = _abi_schema(method_abi.get("outputs", []))
    values = decode(schema, bytes.fromhex(hexstr.removeprefix("0x")))

    if len(values) == 0:
        return None
    if len(values) == 1:
        return values[0]
    return values
