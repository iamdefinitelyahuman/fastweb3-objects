from __future__ import annotations

import keyword
from collections.abc import Iterator, Mapping

from .abi import decode_event, event_signature, event_topic

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

    def __init__(self, raw_log: dict, event_abi: dict):
        self.raw = raw_log
        self.abi = event_abi
        self.name = event_abi["name"]
        self.signature = event_signature(event_abi)
        self.topic = event_topic(event_abi)
        self.address = raw_log["address"]
        self.topics = tuple(raw_log.get("topics", []))
        self.data = raw_log.get("data", "0x")
        self.log_index = raw_log.get("logIndex")
        self.transaction_hash = raw_log.get("transactionHash")
        self.block_number = raw_log.get("blockNumber")
        self.removed = raw_log.get("removed", False)
        self.args = EventArgs(decode_event(event_abi, raw_log))

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


class UnknownEvent:
    decoded = False
    malformed = False
    name = None
    abi = None

    def __init__(self, raw_log: dict, reason: str | None = None):
        self.raw = raw_log
        self.reason = reason
        self.args = EventArgs({})
        self.address = raw_log["address"]
        self.topics = tuple(raw_log.get("topics", []))
        self.topic = self.topics[0] if self.topics else None
        self.data = raw_log.get("data", "0x")
        self.log_index = raw_log.get("logIndex")
        self.transaction_hash = raw_log.get("transactionHash")
        self.block_number = raw_log.get("blockNumber")
        self.removed = raw_log.get("removed", False)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} address={self.address} topic={self.topic}>"


class MalformedEvent(UnknownEvent):
    malformed = True

    def __init__(self, raw_log: dict, event_abi: dict, error: Exception):
        super().__init__(raw_log, reason="malformed")
        self.abi = event_abi
        self.name = event_abi.get("name")
        self.signature = event_signature(event_abi)
        self.topic = event_topic(event_abi)
        self.error = error

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name} error={self.error!r}>"
