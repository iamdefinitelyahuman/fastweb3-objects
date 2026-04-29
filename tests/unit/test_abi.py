import pytest

from fw3_objects.abi import decode_calldata, encode_calldata
from fw3_objects.account import Account
from fw3_objects.errors import ABITypeError, ABIValueError

ADDRESS = "0x000000000000000000000000000000000000dead"


def _method(*inputs):
    return {"name": "foo", "inputs": list(inputs)}


def _encode(inputs, *args):
    return encode_calldata(_method(*inputs), args)


def _roundtrip(inputs, *args):
    method_abi = _method(*inputs)
    return decode_calldata(method_abi, encode_calldata(method_abi, args))


def _normalized(value):
    if isinstance(value, str) and value.startswith("0x") and len(value) == 42:
        return value.lower()
    if isinstance(value, (list, tuple)):
        return tuple(_normalized(item) for item in value)
    return value


def test_abi_errors_subclass_builtins():
    assert issubclass(ABITypeError, TypeError)
    assert issubclass(ABIValueError, ValueError)


@pytest.mark.parametrize(
    ("abi_type", "value", "expected"),
    [
        ("uint8", 255, 255),
        ("uint8", "255", 255),
        ("uint8", "0xff", 255),
        ("uint8", 255.0, 255),
        ("int8", -128, -128),
        ("int8", "-128", -128),
        ("bool", True, True),
        ("string", "0xdeadbeef", "0xdeadbeef"),
        ("bytes", b"\xde\xad\xbe\xef", b"\xde\xad\xbe\xef"),
        ("bytes", "0xdeadbeef", b"\xde\xad\xbe\xef"),
        ("bytes4", b"\xde\xad\xbe\xef", b"\xde\xad\xbe\xef"),
        ("bytes4", "0xdeadbeef", b"\xde\xad\xbe\xef"),
    ],
)
def test_scalar_happy_paths_round_trip(abi_type, value, expected):
    assert _roundtrip([{"name": "value", "type": abi_type}], value) == (expected,)


def test_lowercase_address_string_is_accepted_and_normalized_before_encoding():
    encoded = _encode([{"name": "addr", "type": "address"}], ADDRESS)

    assert encoded.startswith("0x")
    assert _normalized(_roundtrip([{"name": "addr", "type": "address"}], ADDRESS)) == (ADDRESS,)


def test_account_object_is_accepted_for_address():
    account = Account(ADDRESS)
    encoded = _encode([{"name": "addr", "type": "address"}], account)

    assert encoded.startswith("0x")


@pytest.mark.parametrize("value", [[1, "2", "0x03"], (1, "2", "0x03")])
def test_dynamic_array_accepts_lists_and_tuples_and_coerces_recursively(value):
    decoded = _roundtrip([{"name": "values", "type": "uint8[]"}], value)

    assert _normalized(decoded) == ((1, 2, 3),)


def test_fixed_array_validates_length_and_coerces_recursively():
    decoded = _roundtrip([{"name": "values", "type": "uint8[3]"}], [1, "2", "0x03"])

    assert _normalized(decoded) == ((1, 2, 3),)


def test_nested_arrays_coerce_recursively():
    decoded = _roundtrip([{"name": "values", "type": "uint8[2][]"}], [["0x01", 2], [3.0, "4"]])

    assert _normalized(decoded) == (((1, 2), (3, 4)),)


def test_tuple_coerces_positional_components_recursively():
    input_abi = {
        "name": "value",
        "type": "tuple",
        "components": [
            {"name": "addr", "type": "address"},
            {"name": "amounts", "type": "uint8[]"},
            {"name": "payload", "type": "bytes3"},
        ],
    }

    decoded = _roundtrip([input_abi], (ADDRESS, ["1", "0x02"], "0xabcdef"))

    assert _normalized(decoded) == ((ADDRESS, (1, 2), b"\xab\xcd\xef"),)


def test_nested_tuple_array_coerces_recursively():
    input_abi = {
        "name": "values",
        "type": "tuple[]",
        "components": [
            {"name": "addr", "type": "address"},
            {"name": "amount", "type": "uint8"},
        ],
    }

    decoded = _roundtrip([input_abi], [(ADDRESS, "1"), (ADDRESS, 2.0)])

    assert _normalized(decoded) == (((ADDRESS, 1), (ADDRESS, 2)),)


@pytest.mark.parametrize(
    ("abi_type", "value", "error_type"),
    [
        ("bool", 1, ABITypeError),
        ("bool", 0, ABITypeError),
        ("bytes", "deadbeef", ABITypeError),
        ("bytes", "0xabc", ABIValueError),
        ("bytes", "0xzz", ABIValueError),
        ("bytes4", "0xdeadbe", ABIValueError),
        ("string", b"hello", ABITypeError),
        ("uint8", -1, ABIValueError),
        ("uint8", 256, ABIValueError),
        ("int8", -129, ABIValueError),
        ("int8", 128, ABIValueError),
        ("uint8", 1.5, ABIValueError),
        ("uint8", "1.0", ABIValueError),
        ("address", "0xdead", ABIValueError),
        ("address", 123, ABITypeError),
    ],
)
def test_scalar_unhappy_paths_raise_our_errors(abi_type, value, error_type):
    with pytest.raises(error_type):
        _encode([{"name": "value", "type": abi_type}], value)


def test_argument_count_mismatch_raises_abi_type_error():
    with pytest.raises(ABITypeError):
        encode_calldata(_method({"name": "a", "type": "uint8"}), ())


@pytest.mark.parametrize("value", [1, "0x01", {"0": 1}])
def test_array_rejects_non_sequence_values(value):
    with pytest.raises(ABITypeError):
        _encode([{"name": "values", "type": "uint8[]"}], value)


def test_fixed_array_rejects_wrong_length():
    with pytest.raises(ABIValueError):
        _encode([{"name": "values", "type": "uint8[2]"}], [1])


def test_tuple_rejects_dict_values_for_now():
    input_abi = {
        "name": "value",
        "type": "tuple",
        "components": [
            {"name": "addr", "type": "address"},
            {"name": "amount", "type": "uint8"},
        ],
    }

    with pytest.raises(ABITypeError):
        _encode([input_abi], {"addr": ADDRESS, "amount": 1})


def test_tuple_rejects_wrong_length():
    input_abi = {
        "name": "value",
        "type": "tuple",
        "components": [
            {"name": "addr", "type": "address"},
            {"name": "amount", "type": "uint8"},
        ],
    }

    with pytest.raises(ABITypeError):
        _encode([input_abi], (ADDRESS,))
