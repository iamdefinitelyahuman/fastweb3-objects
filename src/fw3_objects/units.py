from decimal import Decimal, InvalidOperation

_UNITS = {
    "gwei": 10**9,
    "ether": 10**18,
}


def to_wei(value):
    if value is None or isinstance(value, int):
        return value
    if not isinstance(value, str):
        raise TypeError("amount must be an int or '<number> <unit>' string")

    parts = value.strip().lower().split()
    if len(parts) != 2:
        raise ValueError("amount string must be formatted as '<number> <unit>'")

    amount, unit = parts
    if unit not in _UNITS:
        raise ValueError(f"Unsupported unit: {unit}")

    try:
        wei = Decimal(amount) * _UNITS[unit]
    except InvalidOperation:
        raise ValueError(f"Invalid amount: {amount}") from None

    if wei != int(wei):
        raise ValueError("Amount cannot be represented as whole wei")
    return int(wei)
