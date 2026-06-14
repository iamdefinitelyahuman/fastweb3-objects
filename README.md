# fastweb3-objects

High-level EVM objects built on [`fastweb3`](https://github.com/iamdefinitelyahuman/fastweb3).

NOTE: This library is still in early alpha development. Prior to a `v1.0.0` (which may never come), expect breaking changes and no backward compatibility between versions.

## Installation

You can install the latest release via `pip`:

```bash
pip install fastweb3-objects
```

Or clone the repository for the most up-to-date version:

```bash
git clone https://github.com/iamdefinitelyahuman/fastweb3-objects.git
cd fastweb3-objects
pip install -e .
```

## Usage

The main entry points are available directly from `fw3_objects`:

```py
>>> from fw3_objects import Account, Accounts, Chain, Contract, Transaction
```

### 1. Connect to a chain

`Chain` is the canonical object for interacting with a specific EVM chain. It accepts a single argument, the chain ID.

```py
>>> chain = Chain(1)
>>> chain
Chain(1)
```

The underlying `fastweb3.Web3` client is created lazily. By default, it uses `fastweb3`'s public RPC discovery and endpoint pooling.

```py
>>> chain.height()
23100000
```

You can access blocks by number, hash, tag, or index syntax:

```py
>>> chain.get_block("latest")
{'number': 23100000, ...}

>>> chain[-1]
{'number': 23100000, ...}
```

To use your own RPC endpoint, configure the chain before first use:

```py
>>> from fw3_objects.chain import configure_chain

>>> configure_chain(1, endpoints=["http://localhost:8545"], use_public_pool=False)
>>> chain = Chain(1)
```

### 2. Use a default chain context

Objects can be bound explicitly to a chain, or they can use the active default chain.

```py
>>> mainnet = Chain(1)

>>> with mainnet.as_default():
...     vitalik = Account("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
...     vitalik.balance()
...
32131215082101779377
```

Strict mode rejects accidental access through another chain while the context is active.

```py
>>> with mainnet.as_default(strict=True):
...     Account("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045").balance(chain=10)
...
ChainMismatch: Cannot use chain 10 while strict default chain 1 is active
```

### 3. Manage accounts

`Accounts` is a [`fastweb3-keypass`](https://github.com/iamdefinitelyahuman/fastweb3-keypass) database wrapper. With no arguments, it opens the default database, creating it first if needed.

```py
>>> accounts = Accounts()
Create password for new Accounts database 'default': ***

>>> accounts
<Accounts unlocked>
```

Accounts created or imported through the database can sign transactions.

```py
>>> acct = accounts.create_account(alias="deployer", set_as_default=True)
>>> acct
<Account 0x0D3AaC9458167352493864Af54D1CBD7F1B8fF68 signable>
```

You can also create watch-only account objects directly from an address.

```py
>>> vitalik = Account("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045", chain=1)
>>> vitalik
<Account 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 watch-only>
```

If an open `Accounts` database already contains the given address and has a private key for it, `Account(address)` automatically binds to that signer.

Chain-aware account helpers expose common RPC operations.

```py
>>> vitalik.balance()
32131215082101779377

>>> vitalik.nonce()
1832

>>> vitalik.bytecode()
b''
```

### 4. Send transactions

Signing accounts can build, sign, broadcast, and watch transactions in a single call.

```py
>>> tx = acct.transact(
...     to="0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
...     value=10**18,
...     chain=1,
... )
```

Native amount fields accept integer wei values, or strings using `ether` and `gwei` units.

```py
>>> acct.transact(
...     to="0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
...     value="1 ether",
...     max_fee_per_gas="30 gwei",
...     max_priority_fee_per_gas="1.5 gwei",
...     chain=1,
... )
```

Only `ether` and `gwei` are supported as string units. Use integers for raw wei values.

The returned `Transaction` object tracks mempool, receipt, replacement, and revert state.

```py
>>> tx.status
<TxStatus.PENDING: -1>

>>> tx.wait()
>>> tx.status
<TxStatus.CONFIRMED: 1>

>>> tx.block_number
23100012

>>> tx.gas_used
21000
```

Pending transactions can be replaced with a higher-fee transaction when the sender is available in an open `Accounts` database.

```py
>>> replacement = tx.replace()
```

### 5. Interact with contracts

`Contract` binds an address, ABI, and chain. ABI can be provided directly, loaded from a JSON file, loaded from cache, or fetched from a supported block explorer.

```py
>>> usdc = Contract("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", chain=1)
```

Explorer ABI lookup is lazy. The constructor returns immediately, and the ABI is resolved on first ABI or method access. Successful lookups are cached by chain and address.

Explorer lookup requires at least one API key:

```py
ETHERSCAN_API_KEY=...
BLOCKSCOUT_API_KEY=...
```

`ETHERSCAN_TOKEN` is also accepted as an alias for `ETHERSCAN_API_KEY`.

When no ABI is provided, `Contract` first checks the local ABI cache. If nothing is cached, it queries configured explorers. `refresh_abi=True` forces a new explorer lookup and updates the cache. `refresh_abi=False` disables explorer lookup and only uses cached ABI data.

Proxy handling is automatic when explorer metadata identifies the address as a proxy. Calls still execute against the proxy address, but the implementation ABI is overlaid with the proxy ABI for method dispatch.

```py
>>> proxy = Contract("0xProxyAddress", chain=1)
>>> proxy.implementationFunction()
```

You can override proxy detection by passing an implementation address, or disable proxy handling entirely with `implementation=False`.

```py
>>> proxy = Contract("0xProxyAddress", chain=1, implementation="0xImplementationAddress")
>>> raw_proxy = Contract("0xProxyAddress", chain=1, implementation=False)
```

Function ABIs are installed as callable attributes.

```py
>>> usdc.name()
'USD Coin'

>>> usdc.symbol()
'USDC'

>>> usdc.balanceOf("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
123456789
```

Read-only functions perform `eth_call`. Nonpayable and payable functions build, sign, broadcast, and return a `Transaction`.

```py
>>> tx = usdc.transfer(
...     "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
...     1_000_000,
...     sender=acct,
... )

>>> tx.wait()
```

Contract methods also expose encoding and decoding helpers.

```py
>>> calldata = usdc.transfer.encode_input(
...     "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
...     1_000_000,
... )

>>> usdc.transfer.decode_input(calldata)
('0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045', 1000000)
```

Overloaded contract methods can usually be resolved by argument count. If that is ambiguous, select the overload by input types.

```py
>>> token.transfer["address,uint256"](
...     "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
...     1_000_000,
...     sender=acct,
... )
```

### 6. Inspect transactions

A transaction hash can be wrapped directly.

```py
>>> tx = Transaction("0xf3063ed07203bc337636f1aa8cb521aa819478df764100950eebea00760a296c", chain=1)
```

The transaction monitor begins watching immediately. Transaction properties wait for the first monitor update before returning, so wrapping a known hash gives you a live object instead of a static receipt snapshot.

```py
>>> tx.sender
<Account 0x0D3AaC9458167352493864Af54D1CBD7F1B8fF68 watch-only>

>>> tx.receiver
<Account 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 watch-only>

>>> tx.value
1000000000000000000
```

`status` reflects the transaction lifecycle.

```py
>>> tx.status
<TxStatus.CONFIRMED: 1>
```

Known statuses are:

- `CONFIRMED`: included successfully
- `REVERTED`: included but reverted
- `PENDING`: visible in the mempool
- `DROPPED`: no longer visible and not replaced
- `REPLACED`: replaced by another transaction with the same sender and nonce
- `UNSEEN`: not yet observed by the node

Receipt-backed properties become available after confirmation.

```py
>>> tx.block_hash
'0x...'

>>> tx.block_number
23100012

>>> tx.transaction_index
42

>>> tx.gas_used
21000

>>> tx.effective_gas_price
18300000000
```

For reverted transactions, revert data and decoded revert reasons are resolved in the background when available.

```py
>>> tx.status
<TxStatus.REVERTED: 0>

>>> tx.revert_data
'0x08c379a...'

>>> tx.revert_reason
'ERC20: transfer amount exceeds balance'
```

Use `wait()` to block until the transaction is finalized and, optionally, has enough confirmations.

```py
>>> tx.wait(required_confs=3)
>>> tx.confirmations()
3
```

Decoded receipt events are available lazily from `tx.events`. The `EventList` object is created once on first access and reused after that.

```py
>>> events = tx.events
>>> len(events)
1
```

### 7. Inspect events

`EventList` is a sequence-like object for decoded receipt logs.

```py
>>> events = tx.events
>>> events
<EventList [...]>
```

Events are available by numeric index.

```py
>>> event = events[0]
>>> event.name
'Transfer'

>>> event.address
<Account 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48 watch-only>
```

Events are also grouped by event name. You can access groups by item lookup or by safe attribute names.

```py
>>> events["Transfer"]
<EventGroup [...]>

>>> events.Transfer
<EventGroup [...]>

>>> events.count("Transfer")
1
```

A decoded event exposes its arguments through `event.args`, item lookup, or safe attribute names.

```py
>>> transfer = events.Transfer[0]

>>> transfer.args
{'from': '0x...', 'to': '0x...', 'value': 1000000}

>>> transfer["value"]
1000000

>>> transfer.args.value
1000000
```

If a group contains exactly one event, arguments can be accessed directly from the group.

```py
>>> events.Transfer["value"]
1000000

>>> events.Transfer.value
1000000
```

Logs that cannot be decoded are preserved as `UnknownEvent` objects. Logs that match an ABI but fail during decoding are preserved as `MalformedEvent` objects.

```py
>>> event.decoded
False

>>> event.malformed
False

>>> event.reason
'abi_missing'
```

If events were decoded before ABI data was available, `enrich()` refreshes ABI data from configured explorers and re-decodes the existing raw logs in place.

```py
>>> events.enrich()
<EventList [...]>
```

`enrich()` requires the event list to have a chain, because explorer ABI lookup is chain-specific.

## Tests

First, install the dev dependencies:

```py
pip install -e ".[dev]"
```

To run the test suite:

```py
pytest
```

## License

This project is licensed under the MIT license.
