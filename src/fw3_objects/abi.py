import re

from Crypto.Hash import keccak
from eth.codecs import abi as _abi

from .errors import ABITypeError, ABIValueError

_ARRAY_RE = re.compile(r"\[(\d*)\]")
_INT_RE = re.compile(r"^(u?int)(\d*)$")
_BYTES_RE = re.compile(r"^bytes(\d*)$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]*$")
_DECIMAL_INT_RE = re.compile(r"^-?[0-9]+$")
_HEX_INT_RE = re.compile(r"^-?0[xX][0-9a-fA-F]+$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


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


def _split_array_type(item_type: str) -> tuple[str, list[int | None]]:
    if "[" not in item_type:
        return item_type, []

    base = item_type[: item_type.index("[")]
    suffix = item_type[len(base) :]
    dims = []

    matches = list(_ARRAY_RE.finditer(suffix))
    if not matches or "".join(i.group(0) for i in matches) != suffix:
        raise ABIValueError(f"Invalid ABI array type: {item_type}")

    for match in matches:
        size = match.group(1)
        dims.append(None if size == "" else int(size))

    return base, dims


def _coerce_args(items: list[dict], values: tuple) -> tuple:
    if len(items) != len(values):
        raise ABITypeError(f"Expected {len(items)} arguments, got {len(values)}")
    return tuple(_coerce_value(item, value) for item, value in zip(items, values, strict=True))


def _coerce_value(item: dict, value):
    base_type, dims = _split_array_type(item["type"])

    if dims:
        return _coerce_array(item, value, base_type, dims)

    if base_type == "address":
        return _coerce_address(value)
    if base_type == "bool":
        return _coerce_bool(value)
    if base_type == "string":
        return _coerce_string(value)
    if base_type == "bytes":
        return _coerce_dynamic_bytes(value)
    if base_type.startswith("bytes"):
        return _coerce_fixed_bytes(base_type, value)
    if base_type.startswith("uint") or base_type.startswith("int"):
        return _coerce_int(base_type, value)
    if base_type == "tuple":
        return _coerce_tuple(item, value)

    raise ABIValueError(f"Unsupported ABI type: {item['type']}")


def _coerce_array(item: dict, value, base_type: str, dims: list[int | None]):
    if not isinstance(value, (list, tuple)):
        raise ABITypeError(f"Expected list or tuple for {item['type']}")

    size = dims[-1]
    if size is not None and len(value) != size:
        raise ABIValueError(f"Expected array of length {size} for {item['type']}, got {len(value)}")

    child = dict(item)
    child["type"] = base_type + "".join("[]" if i is None else f"[{i}]" for i in dims[:-1])
    return tuple(_coerce_value(child, i) for i in value)


def _coerce_tuple(item: dict, value):
    if not isinstance(value, (list, tuple)):
        raise ABITypeError("Expected list or tuple for tuple ABI argument")
    return _coerce_args(item.get("components", []), tuple(value))


def _coerce_address(value) -> str:
    from .account import Account

    if isinstance(value, Account):
        value = value.address

    if not isinstance(value, str):
        raise ABITypeError("Expected address string or Account")
    if not _ADDRESS_RE.fullmatch(value):
        raise ABIValueError(f"Invalid address: {value}")

    return _checksum_address(value)


def _checksum_address(value: str) -> str:
    value = value.removeprefix("0x")
    lower = value.lower()
    k = keccak.new(digest_bits=256)
    k.update(lower.encode())
    digest = k.hexdigest()

    chars = []
    for idx, char in enumerate(lower):
        chars.append(char.upper() if int(digest[idx], 16) >= 8 else char)
    return "0x" + "".join(chars)


def _coerce_bool(value) -> bool:
    if not isinstance(value, bool):
        raise ABITypeError("Expected bool")
    return value


def _coerce_string(value) -> str:
    if not isinstance(value, str):
        raise ABITypeError("Expected string")
    return value


def _coerce_dynamic_bytes(value) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return _coerce_hexbytes(value)
    raise ABITypeError("Expected bytes or 0x-prefixed hex string")


def _coerce_fixed_bytes(item_type: str, value) -> bytes:
    match = _BYTES_RE.fullmatch(item_type)
    if match is None or match.group(1) == "":
        raise ABIValueError(f"Invalid ABI bytes type: {item_type}")

    size = int(match.group(1))
    if size < 1 or size > 32:
        raise ABIValueError(f"Invalid ABI bytes size: {size}")

    value = _coerce_dynamic_bytes(value)
    if len(value) != size:
        raise ABIValueError(f"Expected {item_type} value of length {size}, got {len(value)}")
    return value


def _coerce_hexbytes(value: str) -> bytes:
    if not value.startswith("0x"):
        raise ABITypeError("Expected 0x-prefixed hex string")

    value = value[2:]
    if len(value) % 2:
        raise ABIValueError("Hex string must contain an even number of digits")
    if not _HEX_RE.fullmatch(value):
        raise ABIValueError("Invalid hex string")
    return bytes.fromhex(value)


def _coerce_int(item_type: str, value) -> int:
    match = _INT_RE.fullmatch(item_type)
    if match is None:
        raise ABIValueError(f"Invalid ABI integer type: {item_type}")

    signed = match.group(1) == "int"
    bits = int(match.group(2) or 256)
    if bits < 8 or bits > 256 or bits % 8:
        raise ABIValueError(f"Invalid ABI integer size: {bits}")

    if isinstance(value, bool):
        raise ABITypeError(f"Expected {item_type}")
    if isinstance(value, int):
        coerced = value
    elif isinstance(value, float):
        coerced = _coerce_float_int(item_type, value)
    elif isinstance(value, str):
        coerced = _coerce_string_int(item_type, value)
    else:
        raise ABITypeError(f"Expected {item_type}")

    if signed:
        lower = -(2 ** (bits - 1))
        upper = 2 ** (bits - 1) - 1
    else:
        lower = 0
        upper = 2**bits - 1

    if coerced < lower or coerced > upper:
        raise ABIValueError(f"{item_type} value {coerced} is outside bounds [{lower}, {upper}]")
    return coerced


def _coerce_float_int(item_type: str, value: float) -> int:
    try:
        coerced = int(value)
    except (OverflowError, ValueError) as exc:
        raise ABIValueError(f"Invalid {item_type} value: {value}") from exc

    if value != coerced:
        raise ABIValueError(f"Expected integral float for {item_type}")
    return coerced


def _coerce_string_int(item_type: str, value: str) -> int:
    try:
        if _HEX_INT_RE.fullmatch(value):
            return int(value, 16)
        if _DECIMAL_INT_RE.fullmatch(value):
            return int(value, 10)
        raise ValueError
    except ValueError as exc:
        raise ABIValueError(f"Invalid {item_type} string: {value}") from exc


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
    inputs = method_abi.get("inputs", [])
    schema = _abi_schema(inputs)
    coerced = _coerce_args(inputs, args)
    data = function_selector(method_abi) + encode(schema, coerced)
    return f"0x{data.hex()}"


def decode_returndata(method_abi: dict, hexstr: str):
    schema = _abi_schema(method_abi.get("outputs", []))
    values = decode(schema, bytes.fromhex(hexstr.removeprefix("0x")))

    if len(values) == 0:
        return None
    if len(values) == 1:
        return values[0]
    return values
