import pytest

from fw3_objects import abi
from fw3_objects.account import Account
from fw3_objects.chain import Chain
from fw3_objects.contract import Contract
from fw3_objects.events import (
    Event,
    EventArgs,
    EventGroup,
    MalformedEvent,
    UnknownEvent,
    event_topic,
)

ADDRESS = "0x000000000000000000000000000000000000dead"
FROM = "0x0000000000000000000000000000000000000001"
TO = "0x0000000000000000000000000000000000000002"
TX_HASH = "0x" + "aa" * 32


TRANSFER_ABI = {
    "type": "event",
    "name": "Transfer",
    "inputs": [
        {"name": "from", "type": "address", "indexed": True},
        {"name": "to", "type": "address", "indexed": True},
        {"name": "value", "type": "uint256", "indexed": False},
    ],
}


MESSAGE_ABI = {
    "type": "event",
    "name": "Message",
    "inputs": [
        {"name": "sender", "type": "address", "indexed": True},
        {"name": "message", "type": "string", "indexed": True},
        {"name": "value", "type": "uint256", "indexed": False},
    ],
}


def _topic(schema, values):
    return "0x" + abi.encode(schema, values).hex()


def _transfer_log(value=123):
    return {
        "address": ADDRESS,
        "topics": [
            event_topic(TRANSFER_ABI),
            _topic("(address)", (FROM,)),
            _topic("(address)", (TO,)),
        ],
        "data": "0x" + abi.encode("(uint256)", (value,)).hex(),
        "logIndex": 7,
        "transactionHash": TX_HASH,
        "blockNumber": 12,
    }


def test_event_decodes_indexed_topics_and_data():
    event = Event(_transfer_log(), TRANSFER_ABI)

    assert event.name == "Transfer"
    assert event.signature == "Transfer(address,address,uint256)"
    assert isinstance(event.address, Account)
    assert str(event.address).lower() == ADDRESS.lower()
    assert event.log_index == 7
    assert event.transaction_hash == TX_HASH
    assert event.block_number == 12
    assert event.args["from"] == FROM
    assert event.args.from_ == FROM
    assert event.args["to"] == TO
    assert event.args.to == TO
    assert event.args["value"] == 123
    assert event["value"] == 123


def test_event_preserves_indexed_dynamic_values_as_topic_hashes():
    log = {
        "address": ADDRESS,
        "topics": [
            event_topic(MESSAGE_ABI),
            _topic("(address)", (FROM,)),
            "0x" + "11" * 32,
        ],
        "data": "0x" + abi.encode("(uint256)", (123,)).hex(),
    }

    event = Event(log, MESSAGE_ABI)

    assert event.args.sender == FROM
    assert event.args.message == "0x" + "11" * 32
    assert event.args.value == 123


def test_event_rejects_wrong_topic_count():
    log = _transfer_log()
    log["topics"] = log["topics"][:2]

    with pytest.raises(ValueError, match="Expected 3 topics, got 2"):
        Event(log, TRANSFER_ABI)


def test_event_rejects_wrong_topic_zero():
    log = _transfer_log()
    log["topics"][0] = "0x" + "99" * 32

    with pytest.raises(ValueError, match="does not match event topic"):
        Event(log, TRANSFER_ABI)


def test_event_rejects_malformed_data():
    log = _transfer_log()
    log["data"] = "0x1234"

    with pytest.raises(Exception):
        Event(log, TRANSFER_ABI)


def test_event_args_attribute_access_is_best_effort():
    args = EventArgs(
        {
            "from": 1,
            "from_": 2,
            "items": 3,
            "not-valid": 4,
        }
    )

    assert args["from"] == 1
    assert args.from_ == 1
    assert args["from_"] == 2
    assert args.items_ == 3

    with pytest.raises(AttributeError):
        args.not_valid


def test_event_args_supports_positional_access_and_mapping_methods():
    args = EventArgs({"a": 1, "b": 2})

    assert args[0] == 1
    assert args[1] == 2
    assert list(args.keys()) == ["a", "b"]
    assert list(args.items()) == [("a", 1), ("b", 2)]
    assert dict(args) == {"a": 1, "b": 2}


def test_event_group_proxies_single_event_arguments():
    event = Event(_transfer_log(), TRANSFER_ABI)
    group = EventGroup([event])

    assert len(group) == 1
    assert group[0] is event
    assert group["value"] == 123
    assert group.from_ == FROM


def test_event_group_rejects_argument_access_when_ambiguous():
    group = EventGroup(
        [
            Event(_transfer_log(1), TRANSFER_ABI),
            Event(_transfer_log(2), TRANSFER_ABI),
        ]
    )

    assert len(group) == 2
    assert group[0].args.value == 1
    assert group[1].args.value == 2

    with pytest.raises(ValueError, match="Cannot access argument 'value' on 2 events"):
        group["value"]

    with pytest.raises(AttributeError):
        group.value


def test_unknown_and_malformed_events_preserve_raw_log_metadata():
    log = _transfer_log()
    unknown = UnknownEvent(log, reason="abi_missing")
    err = ValueError("bad log")
    malformed = MalformedEvent(log, TRANSFER_ABI, err)

    assert unknown.decoded is False
    assert unknown.malformed is False
    assert unknown.reason == "abi_missing"
    assert isinstance(unknown.address, Account)
    assert str(unknown.address).lower() == ADDRESS.lower()
    assert unknown.topic == event_topic(TRANSFER_ABI)
    assert malformed.decoded is False
    assert malformed.malformed is True
    assert malformed.name == "Transfer"
    assert malformed.error is err


def test_events_use_chain_bound_address_objects_when_chain_is_provided():
    chain = Chain(1)

    event = Event(_transfer_log(), TRANSFER_ABI, chain=chain)
    unknown = UnknownEvent(_transfer_log(), reason="abi_missing", chain=chain)
    malformed = MalformedEvent(_transfer_log(), TRANSFER_ABI, ValueError("bad log"), chain=chain)

    assert isinstance(event.address, Contract)
    assert str(event.address).lower() == ADDRESS.lower()
    assert isinstance(unknown.address, Account)
    assert str(unknown.address).lower() == ADDRESS.lower()
    assert isinstance(malformed.address, Contract)
    assert str(malformed.address).lower() == ADDRESS.lower()


def test_event_list_decodes_known_events_and_builds_groups(monkeypatch):
    class FakeContract:
        abis = {ADDRESS.lower(): [TRANSFER_ABI]}

        def __init__(self, address, *, chain=None, refresh_abi=False):
            self.address = Account(address)
            self.chain = chain
            self.refresh_abi = refresh_abi

        def __str__(self):
            return str(self.address)

        @property
        def abi(self):
            try:
                return self.abis[str(self.address).lower()]
            except KeyError:
                raise AttributeError("abi") from None

    monkeypatch.setattr("fw3_objects.events.Contract", FakeContract)

    from fw3_objects.events import EventList

    events = EventList([_transfer_log(1), _transfer_log(2)], chain=1)

    assert len(events) == 2
    assert events[0].args.value == 1
    assert events[1].args.value == 2
    assert events.count("Transfer") == 2
    assert list(events.keys()) == ["Transfer"]
    assert events["Transfer"][0] is events[0]
    assert events.Transfer[1] is events[1]


def test_event_list_creates_unknown_events_for_missing_abi_and_topic(monkeypatch):
    missing_abi_address = "0x000000000000000000000000000000000000beef"

    class FakeContract:
        abis = {ADDRESS.lower(): [TRANSFER_ABI]}

        def __init__(self, address, *, chain=None, refresh_abi=False):
            self.address = Account(address)

        @property
        def abi(self):
            try:
                return self.abis[str(self.address).lower()]
            except KeyError:
                raise AttributeError("abi") from None

    monkeypatch.setattr("fw3_objects.events.Contract", FakeContract)

    from fw3_objects.events import EventList

    no_abi_log = _transfer_log()
    no_abi_log["address"] = missing_abi_address
    no_topic_log = _transfer_log()
    no_topic_log["topics"][0] = "0x" + "99" * 32

    events = EventList([no_abi_log, no_topic_log], chain=1)

    assert len(events) == 2
    assert isinstance(events[0], UnknownEvent)
    assert events[0].reason == "abi_missing"
    assert isinstance(events[1], UnknownEvent)
    assert events[1].reason == "topic_missing"
    assert list(events.keys()) == []


def test_event_list_creates_malformed_events_for_matched_abis_that_fail_decode(monkeypatch):
    class FakeContract:
        def __init__(self, address, *, chain=None, refresh_abi=False):
            self.address = Account(address)

        @property
        def abi(self):
            return [TRANSFER_ABI]

    monkeypatch.setattr("fw3_objects.events.Contract", FakeContract)

    from fw3_objects.events import EventList

    log = _transfer_log()
    log["data"] = "0x1234"
    events = EventList([log], chain=1)

    assert len(events) == 1
    assert isinstance(events[0], MalformedEvent)
    assert events[0].name == "Transfer"
    assert events[0].malformed is True
    assert events.count("Transfer") == 1
    assert events["Transfer"][0] is events[0]


def test_event_list_enrich_retries_with_abi_refresh_enabled(monkeypatch):
    calls = []

    class FakeContract:
        def __init__(self, address, *, chain=None, refresh_abi=False):
            self.address = Account(address)
            self.refresh_abi = refresh_abi
            calls.append(refresh_abi)

        @property
        def abi(self):
            if self.refresh_abi is None:
                return [TRANSFER_ABI]
            raise AttributeError("abi")

    monkeypatch.setattr("fw3_objects.events.Contract", FakeContract)

    from fw3_objects.events import EventList

    events = EventList([_transfer_log()], chain=1)

    assert isinstance(events[0], UnknownEvent)

    enriched = events.enrich()

    assert enriched is events
    assert events[0].name == "Transfer"
    assert False in calls
    assert None in calls


def test_event_list_enrich_requires_chain():
    from fw3_objects.events import EventList

    events = EventList([_transfer_log()])

    with pytest.raises(ValueError, match="Cannot enrich events without a chain"):
        events.enrich()


def test_event_list_enrich_during_init_uses_abi_refresh_enabled(monkeypatch):
    calls = []

    class FakeContract:
        def __init__(self, address, *, chain=None, refresh_abi=False):
            self.address = Account(address)
            self.refresh_abi = refresh_abi
            calls.append(refresh_abi)

        @property
        def abi(self):
            if self.refresh_abi is None:
                return [TRANSFER_ABI]
            raise AttributeError("abi")

    monkeypatch.setattr("fw3_objects.events.Contract", FakeContract)

    from fw3_objects.events import EventList

    events = EventList([_transfer_log()], chain=1, enrich=True)

    assert events[0].name == "Transfer"
    assert calls == [None, False]
