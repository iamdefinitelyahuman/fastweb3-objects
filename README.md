# fastweb3-objects

High-level EVM objects built on `fastweb3`.

NOTE: This library is still in early alpha development. Prior to a `v1.0.0` (which may never come), expect breaking changes and no backward compatibility between versions.

## Installation

You can install the latest release via `pip`:

    pip install fastweb3-objects

Or clone the repository for the most up-to-date version:

    git clone https://github.com/iamdefinitelyahuman/fastweb3-objects.git
    cd fastweb3-objects
    pip install -e .

## Usage

The main entry points are available directly from `fw3_objects`:

    >>> from fw3_objects import Account, Accounts, Chain, Contract, Transaction

### 1. Connect to a chain

`Chain` is the canonical object for interacting with a specific EVM chain.

    >>> chain = Chain(1)
    >>> chain
    Chain(1)

The underlying `fastweb3.Web3` client is created lazily. By default, it uses `fastweb3`'s public RPC discovery and endpoint pooling.

    >>> chain.height()
    23100000

You can access blocks by number, hash, tag, or index syntax:

    >>> chain.get_block("latest")
    {'number': 23100000, ...}

    >>> chain[-1]
    {'number': 23100000, ...}

To use your own RPC endpoint, configure the chain before first use:

    >>> from fw3_objects.chain import configure_chain

    >>> configure_chain(1, endpoints=["http://localhost:8545"], use_public_pool=False)
    >>> chain = Chain(1)

### 2. Use a default chain context

Objects can be bound explicitly to a chain, or they can use the active default chain.

    >>> mainnet = Chain(1)

    >>> with mainnet.as_default():
    ...     vitalik = Account("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    ...     vitalik.balance()
    ...
    32131215082101779377

Strict mode rejects accidental access through another chain while the context is active.

    >>> with mainnet.as_default(strict=True):
    ...     Account("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045").balance()

### 3. Manage accounts

`Accounts` is a `fastweb3-keypass` database wrapper. With no arguments, it opens the default database, creating it first if needed.

    >>> accounts = Accounts()
    Create password for new Accounts database 'default': ***

    >>> accounts
    <Accounts unlocked>

Accounts created or imported through the database can sign transactions.

    >>> acct = accounts.create_account(alias="deployer", set_as_default=True)
    >>> acct
    <Account 0x0D3AaC9458167352493864Af54D1CBD7F1B8fF68 signable>

You can also create watch-only account objects directly from an address.

    >>> vitalik = Account("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045", chain=1)
    >>> vitalik
    <Account 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 watch-only>

Chain-aware account helpers expose common RPC operations.

    >>> vitalik.balance()
    32131215082101779377

    >>> vitalik.nonce()
    1832

    >>> vitalik.bytecode()
    b''

### 4. Send transactions

Signing accounts can build, sign, broadcast, and watch transactions in a single call.

    >>> tx = acct.transact(
    ...     to="0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
    ...     value=10**18,
    ...     chain=1,
    ... )

The returned `Transaction` object tracks mempool, receipt, replacement, and revert state.

    >>> tx.status
    <TxStatus.PENDING: -1>

    >>> tx.wait()
    >>> tx.status
    <TxStatus.CONFIRMED: 1>

    >>> tx.block_number
    23100012

    >>> tx.gas_used
    21000

Pending transactions can be replaced with a higher-fee transaction when the sender is available in an open `Accounts` database.

    >>> replacement = tx.replace()

### 5. Interact with contracts

`Contract` binds an address, ABI, and chain. ABI can be provided directly, loaded from a JSON file, loaded from cache, or fetched from a supported block explorer.

    >>> usdc = Contract("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", chain=1)

Function ABIs are installed as callable attributes.

    >>> usdc.name()
    'USD Coin'

    >>> usdc.symbol()
    'USDC'

    >>> usdc.balanceOf("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    123456789

Read-only functions perform `eth_call`. Nonpayable and payable functions build, sign, broadcast, and return a `Transaction`.

    >>> tx = usdc.transfer(
    ...     "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
    ...     1_000_000,
    ...     sender=acct,
    ... )

    >>> tx.wait()

Contract methods also expose encoding and decoding helpers.

    >>> calldata = usdc.transfer.encode_input(
    ...     "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
    ...     1_000_000,
    ... )

    >>> usdc.transfer.decode_input(calldata)
    ('0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045', 1000000)

### 6. Inspect transactions

A transaction hash can be wrapped directly.

    >>> tx = Transaction("0xf3063ed07203bc337636f1aa8cb521aa819478df764100950eebea00760a296c", chain=1)

Transaction properties wait for the first monitor update before returning.

    >>> tx.sender
    <Account 0x0D3AaC9458167352493864Af54D1CBD7F1B8fF68 watch-only>

    >>> tx.receiver
    <Account 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 watch-only>

    >>> tx.value
    1000000000000000000

Decoded receipt events are available lazily from `tx.events`.

    >>> len(tx.events)
    1

## Tests

First, install the dev dependencies:

    pip install -e ".[dev]"

To run the test suite:

    pytest

## License

This project is licensed under the MIT license.
