from __future__ import annotations

import json

import httpx

from fw3_objects.errors import ABINotFound, ExplorerError, ExplorerRateLimited

BASE_URL = "https://api.blockscout.com/v2/api"
RATE_LIMIT_COOLDOWN = 0.25


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None

    try:
        return max(float(value), 0.0)
    except ValueError:
        return None


def get_abi(chain_id: int, address: str, api_key: str) -> list[dict]:
    params = {
        "chain_id": int(chain_id),
        "module": "contract",
        "action": "getabi",
        "address": address,
        "apikey": api_key,
    }

    try:
        response = httpx.get(BASE_URL, params=params, timeout=10)
    except httpx.HTTPError as exc:
        raise ExplorerError(str(exc)) from exc

    if response.status_code == 429:
        raise ExplorerRateLimited("blockscout", _retry_after(response))

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ExplorerError(str(exc)) from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise ExplorerError("Invalid Blockscout response") from exc

    status = data.get("status")
    result = data.get("result")

    if status == "0":
        message = str(data.get("message", ""))
        if "rate limit" in message.lower():
            raise ExplorerRateLimited("blockscout", None)
        raise ABINotFound(str(result or message or "ABI not found"))

    if not result:
        raise ABINotFound("ABI not found")

    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except ValueError as exc:
            raise ExplorerError("Invalid Blockscout ABI JSON") from exc
    else:
        parsed = result

    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise ExplorerError("Invalid Blockscout ABI")

    return parsed
