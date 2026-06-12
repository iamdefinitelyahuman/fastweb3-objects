from __future__ import annotations

import keyword
from collections.abc import Iterator, Mapping

from .abi import decode_event, event_signature, event_topic
from .account import Account
from .chain import Chain
from .contract import Contract
from .errors import ABINotFound, ExplorerError

_EVENT_ARG_RESERVED = {
    "get",
    "items",
    "keys",
    "values",
}


def _attr_name(name: str) -> str | None:
    if not name.isidentifier():
        return None
    if name.startswith("__") and name.endswith("__"):
        return None
    if keyword.iskeyword(name) or name in _EVENT_ARG_RESERVED:
        return f"{name}_"
    return name


def _address(address: str, chain: Chain | int | None):
    if chain is not None:
        return Contract(address, chain=chain, refresh_abi=False)
    return Account(address)


def _topic_id(value) -> str:
    if isinstance(value, bytes):
        return "0x" + value.hex()
    return value.lower()


def _log_address(raw_log: dict) -> str:
    return str(Account(raw_log["address"])).lower()


class EventArgs(Mapping):
    def __init__(self, values: Mapping[str, object]):
        self._values = dict(values)
        self._attrs = {}

        for name in self._values:
            attr = _attr_name(name)
            if attr is None or attr in self._attrs or hasattr(type(self), attr):
                continue
            self._attrs[attr] = name

    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self._values.values())[key]
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __getattr__(self, name: str):
        try:
            return self._values[self._attrs[name]]
        except KeyError:
            raise AttributeError(name) from None

    def __repr__(self) -> str:
        return repr(self._values)


class Event:
    decoded = True
    malformed = False

    def __init__(self, raw_log: dict, event_abi: dict, *, chain: Chain | int | None = None):
        self.raw = raw_log
        self.abi = event_abi
        self.name = event_abi["name"]
        self.signature = event_signature(event_abi)
        self.topic = event_topic(event_abi)
        self.address = _address(raw_log["address"], chain)
        self.topics = tuple(raw_log.get("topics", []))
        self.data = raw_log.get("data", "0x")
        self.log_index = raw_log.get("logIndex")
        self.transaction_hash = raw_log.get("transactionHash")
        self.block_number = raw_log.get("blockNumber")
        self.removed = raw_log.get("removed", False)
        self.args = EventArgs(decode_event(event_abi, raw_log))
        self.chain = Chain(chain) if chain is not None else None

    def __getitem__(self, key):
        return self.args[key]

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name} {dict(self.args)!r}>"


class EventGroup:
    def __init__(self, events):
        self._events = tuple(events)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._events[key]
        if isinstance(key, str):
            if len(self._events) != 1:
                raise ValueError(f"Cannot access argument {key!r} on {len(self._events)} events")
            return self._events[0][key]
        raise TypeError("EventGroup indices must be integers or argument names")

    def __iter__(self):
        return iter(self._events)

    def __len__(self):
        return len(self._events)

    def __bool__(self):
        return bool(self._events)

    def __getattr__(self, name: str):
        if len(self._events) != 1:
            raise AttributeError(name)
        try:
            return getattr(self._events[0].args, name)
        except AttributeError:
            raise AttributeError(name) from None

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {list(self._events)!r}>"


class EventList:
    def __init__(
        self,
        raw_logs,
        *,
        chain: Chain | int | None = None,
        enrich: bool = False,
    ):
        self.raw_logs = tuple(raw_logs)
        self.chain = Chain(chain) if chain is not None else None
        self._events = tuple(self._decode_logs(enrich))
        self._groups = {}

        for event in self._events:
            if event.name is None:
                continue
            self._groups.setdefault(event.name, []).append(event)

        self._groups = {name: EventGroup(events) for name, events in self._groups.items()}
        self._attrs = {}

        for name in self._groups:
            attr = _attr_name(name)
            if attr is None or attr in self._attrs or hasattr(type(self), attr):
                continue
            self._attrs[attr] = name

    def _decode_logs(self, enrich: bool):
        refresh_abi = None if enrich else False
        topic_maps = self._topic_maps(refresh_abi)

        for raw_log in self.raw_logs:
            address = _log_address(raw_log)
            topics = raw_log.get("topics", [])
            topic = _topic_id(topics[0]) if topics else None
            topic_map = topic_maps.get(address)

            if topic_map is None:
                yield UnknownEvent(raw_log, reason="abi_missing", chain=self.chain)
                continue

            event_abi = topic_map.get(topic)
            if event_abi is None:
                yield UnknownEvent(raw_log, reason="topic_missing", chain=self.chain)
                continue

            try:
                yield Event(raw_log, event_abi, chain=self.chain)
            except Exception as exc:
                yield MalformedEvent(raw_log, event_abi, exc, chain=self.chain)

    def _topic_maps(self, refresh_abi: bool | None) -> dict[str, dict[str, dict]]:
        topic_maps = {}

        for address in {_log_address(log) for log in self.raw_logs}:
            try:
                contract = Contract(address, chain=self.chain, refresh_abi=refresh_abi)
                contract_abi = contract.abi
            except AttributeError:
                continue
            except (ABINotFound, ExplorerError):
                continue

            topic_map = {}
            for item in contract_abi:
                if item.get("type") != "event" or item.get("anonymous", False):
                    continue
                topic_map[event_topic(item)] = item

            topic_maps[address] = topic_map

        return topic_maps

    def enrich(self):
        return EventList(self.raw_logs, chain=self.chain, enrich=True)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._events[key]
        if isinstance(key, str):
            return self._groups[key]
        raise TypeError("EventList indices must be integers or event names")

    def __iter__(self):
        return iter(self._events)

    def __len__(self):
        return len(self._events)

    def __bool__(self):
        return bool(self._events)

    def __getattr__(self, name: str):
        try:
            return self._groups[self._attrs[name]]
        except KeyError:
            raise AttributeError(name) from None

    def count(self, name: str) -> int:
        return len(self._groups.get(name, ()))

    def keys(self):
        return self._groups.keys()

    def items(self):
        return self._groups.items()

    def values(self):
        return self._groups.values()

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {list(self._events)!r}>"


class UnknownEvent:
    decoded = False
    malformed = False
    name = None
    abi = None

    def __init__(
        self, raw_log: dict, reason: str | None = None, *, chain: Chain | int | None = None
    ):
        self.raw = raw_log
        self.reason = reason
        self.args = EventArgs({})
        self.address = Account(raw_log["address"])
        self.topics = tuple(raw_log.get("topics", []))
        self.topic = self.topics[0] if self.topics else None
        self.data = raw_log.get("data", "0x")
        self.log_index = raw_log.get("logIndex")
        self.transaction_hash = raw_log.get("transactionHash")
        self.block_number = raw_log.get("blockNumber")
        self.removed = raw_log.get("removed", False)
        self.chain = Chain(chain) if chain is not None else None

    def __repr__(self) -> str:
        return f"<{type(self).__name__} address={self.address} topic={self.topic}>"


class MalformedEvent(UnknownEvent):
    malformed = True

    def __init__(
        self, raw_log: dict, event_abi: dict, error: Exception, *, chain: Chain | int | None = None
    ):
        super().__init__(raw_log, reason="malformed", chain=chain)
        self.address = _address(raw_log["address"], chain)
        self.abi = event_abi
        self.name = event_abi.get("name")
        self.signature = event_signature(event_abi)
        self.topic = event_topic(event_abi)
        self.error = error

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name} error={self.error!r}>"
