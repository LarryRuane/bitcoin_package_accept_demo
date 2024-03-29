# bitcoin_package_accept_demo

## Summary

Demonstrate one possible bitcoin mempool package minimum fee
acceptance algorithm.

This repository grew out of my review of Bitcoin Core
[PR 24152](https://github.com/bitcoin/bitcoin/pull/24152).

As an alternative to cloning this repository, you can run the
demo at [this python playground](https://code.sololearn.com/c3Z6xssy8km5)

A package is a topologically-sorted list of transactions (a
([DAG](https://en.wikipedia.org/wiki/Directed_acyclic_graph)),
each transaction having an absolute fee, size (actually, virtual size,
but we'll refer to size here), and list of parents.
PR 24152 restricts the package to having
[one child with multiple
parents](https://gist.github.com/glozow/dc4e9d5c5b14ade7cdfac40f43adb18a#packages-are-multi-parent-1-child).
Also, PR 24152 accepts all the transactions in a package
(after deduplication, removing transactions that are already
in the mempool) or rejects the package entirely.

This demo investigates what happens if we generalize
the allowed input package to be an arbitrary DAG, and also
if we allow a subset of the package to be accepted (rather than
all-or-nothing). It does not attempt to address RBF.

Update: This demo also uses this algorithm to calculate
mempool UTXO effective-value reduction, see below.

## Algorithm requirements

The algorithm takes a package and a minimum fee rate as inputs
and generates a subset of the package (filters it) so that the result
includes only those transactions and groups of transactions that
satisfy the minimum feerate. These are the transactions
from the package that should be accepted into the mempool and relayed
to other nodes (assuming all the other validation checks pass). The
algorithm accepts a transaction by including it in a _result list_.

The procedure is trivial if all transactions are independent of each
other: simply calculate each transaction's feerate (its fee divided
by its size), and if it's greater than or equal to the minimum fee
rate, then add the transaction to the result list.

#### dependencies

But it's tricky when there are dependencies. If a package has
two transactions, A and its child B (B has an input that refers to
one of A's outputs), there are three possible results:

- []
- [A]
- [A, B]

If neither transaction has a sufficiently high feerate, the
result will be the empty list. 
If only A has the minimum feerate, we can take it alone.
We can't take only B as it would be
an invalid transaction (it would have a nonexistent input).
We can take both if the _overall_ feerate (the sum of the two feess
divided by the sum of the two sizes) is at least the minimum feerate.

#### miner-incentive compatible

An additional requirement, which is somewhat subtle, is that the result
must be the most miner-incentive (miner-revenue maximizing) possible.
I haven't proven that this algorithm accomplishes that, but experiments
seem to show that it is. Here's an example:

Suppose A has a sufficient
feerate by itself, and B does not, but the combination of A and B
has a sufficient feerate. Accepting the package [A, B]
would satisfy the minimum feerate. However, doing so would implement a
[parent-pays-for-child](https://gist.github.com/glozow/dc4e9d5c5b14ade7cdfac40f43adb18a#always-try-individual-submission-first)
policy, which is not miner-incentive compatible.
The reason is that taking the
parent, A, by itself has an even better feerate,
and B is not needed to mine A. So the algorithm should select only A.

#### mempool UTXO effective-value decrement (fee bumping)

It turns out this algorithm can also be used to compute the amount
to reduce the effective value of a mempool output (as a way of
bumping, increasing the fee) of a proposed transaction. This would
only be needed if the parent transaction's feerate is lower than
the proposed transaction's desired feerate. See
[PR 26152](https://github.com/bitcoin/bitcoin/pull/26152). What
was considered the minimum feerate above can instead be interpreted
as the desired feerate when constructing a new transaction.

This simulation doesn't explicitly model outputs (only implicitly
by an input referring to a parent transaction, so that parent obviously
must have a corresponding output), so in this simulation, we calculate
the EV decrement for every transaction, as if every transaction has an
output that we can spend. In the real world, one would have to filter out
of the result list transaction outputs that we don't have spending
keys for, as well as transactions that have no unspent outputs.

The transactions that pass the minimum feerate test (above) can be
used as inputs without needing to reduce their effective value,
because using them as an input would not decrease our ancestor
feerate. Those transactions that _do not_ pass the minimum
feerate test can still be used, but _do_ need their effective
value reduced (the reduction going to fees) when passed to
coin selection. This algorithm computes the needed amount.

For this use case, the graph given to the algorithm must
include mempool transactions with UTXOs that the local wallet
has spending keys for, _and_ all of their related transactions,
which means all of their mempool ancestors and decendants,
recursively. (It's okay to include more)

## Algorithm details (Python script)

The class `fee_sz` encapsulates a fee (in sats) and a size
(bytes, or more accurately, virtual bytes, vbytes), which are
the two values needed to calculate a feerate. But the algorithm
needs to keep them separate in order to "add" the feerates
of multiple transactions. We do this by adding the fees, and
adding the sizes.

Transactions are idenfied by single lower-case letters,
since this is often how they're shown in diagrams.
So `'a'` and `'b'` the txids of the first two transactions.

The algorithm, `partition_by_feerate()` is so named because
it takes as input a feerate and a graph of transactions,
and partitions the transactions into two subsets, those
with feerates, accounting for ancestors (which is where
the complexity comes in), are greater than or equal to
the given feerate, and those that are not.

The function iterates through the
transactions (each a node in the graph) sequentially
(they must be provided in topologically sorted order),
ancestors to decendants (left-to-right, or top to bottom).
We calculate its `ancestor_fee_sz`
by adding its own fee and size to those of
its parents and their parents, recursively.
The results are stored in the current tx's entry in the
`ancestor_fee_szs` dict for the benefit of
the transactions to follow, a type of
[_memoization_](https://en.wikipedia.org/wiki/Memoization).
This is why it's not necessary to
recursively visit parents of parents; parents will already
have their ancestor values set.

If the resulting ancestor feerate is at least the
feerate argument, then we add this tx and all its
ancestors to `high_fee_set` ("set" here meaning Python set).
We do this using the common graph-traversal
technique of the "todo list" which we work on until it's empty;
this obviates the need for actual recursion.

An important point is that the transactions that we're
adding to the `high_fee_set` set should _no longer_ be
considered part of the (input) graph. This is because they
no longer need to be "paid for" by descendants.
Python doesn't allow you
to delete an item from a dictionary while you're iterating it,
so we don't remove these transactions, but instead, a tx's presence in the
`high_fee_set` set makes it no longer _logically_ part of the graph.
Thus you see the checks for a tx
being `in high_fee_set` and skipped if so.

The first version of this algorithm made a single pass through
the graph, but then I discovered a problem, which is demonstrated
by the test case "This graph shows why multiple passes may be
needed". Transaction A has two
children, B and C. A has a low feerate, and either B or
C may be able to pay for parent A. First, the algorithm
skips A (doesn't add it to `high_fee_set`) since it doesn't pass the
feerate test. Now we look
at B. Its feerate is sufficient by itself, but since it has a parent A,
we must calculate the feerate of the
two together, which barely doesn't pass the test. So they
remain (are not added to `high_fee_set`). Now we evaluate C. It
has a higher feerate than B, high enough that C and A
are included in the result. We're left with just B, which
_by itself_ has a suffient feerate, but we didn't include it.

Notice that if we had visited C before B, which would still
conform to topological sort order, all three transactions
would have been included -- which is clearly the desired
outcome.

The easist way to fix this seems to be to make multiple
passes over the input. The first pass moves A and C to
to `high_fee_set`; the second pass moves B (since it no longer
has a parent to pay for). We keep looping
until we make no progress, a common technique in
dynamic programming.

A requirement to
do multiple passes isn't great, and is a possible DoS
concern, but I can't imagine this would be a problem in
practice. Even if the graph has the maximum number of 25
transactions, the first pass should pick up almost
all that should be included, leaving very few for
a second (or subsequent) pass. The passes become faster
too, since fewer transactions remain each time.

The `partition_by_feerate()` function returns two lists,
the transactions that pass the feerate test, `result_ge`,
meaning their ancestor feerates are greater than or equal ("ge")
to the specified feerate, and the remaining transactions,
those that didn't pass the feerate test, `result_lt`, these
transactions's ancestor feerates are less than the one
specified. The `result_lt` list also includes each
transaction's ancestor feerate, since it's needed to compute
the output effective-value decrement amount (next section).

#### Mempool UTXO effective-value decrement (fee bumping)

`partition_by_feerate()` returns the list of transactions that
should have their effective values reduced, and the amount to
reduce them by, in `result_lt` (the ancestor feerates of these
transactions are "less than" the requested feerate). If any
are used in constructing the new transaction, they should
be given to coin selection with the output amounts reduced by
the returned values.

Any transactions _not_ in this list can, of course, be used
as inputs to the transaction being constructed (they should
be given to coin selection), but they don't need to have
their effective values reduced.

### test structure

The test is in the form of three levels. The outer level is
a series of graphs (DAGs), each of which has
one or more per-transaction fee and size assignments, and each of
those in turn can be evaluated at various feerates.
It should be pretty simple to add new test cases.

## Output

Here's the current output of the demo program:
```

-------------------------------- graph:
Simplest possible graph, a single tx
{'a': []}

trivial case, a single tx, feerate is individually calculated
{'a': (400, 100)}
  minfeerate feerate=5 total=fee_sz(sats=0, size=0) pass=[] actual_rate=0.00
  ev_decrement=[('a', '100')]
  minfeerate feerate=4 total=fee_sz(sats=400, size=100) pass=['a'] actual_rate=4.00
  ev_decrement=[]
  minfeerate feerate=1 total=fee_sz(sats=400, size=100) pass=['a'] actual_rate=4.00
  ev_decrement=[]

-------------------------------- graph:
Simple two-transaction parent-child case
{'a': [], 'b': ['a']}

child pays for parent: low-fee parent A, high-fee child B
{'a': (100, 300), 'b': (700, 100)}
  minfeerate feerate=2 total=fee_sz(sats=800, size=400) pass=['a', 'b'] actual_rate=2.00
  ev_decrement=[]
  minfeerate feerate=2.1 total=fee_sz(sats=0, size=0) pass=[] actual_rate=0.00
  ev_decrement=[('a', '530.00'), ('b', '40.00')]

unsuccessful parent pays for child: high-fee parent A, low-fee child B
{'a': (900, 300), 'b': (100, 1000)}
  minfeerate feerate=1 total=fee_sz(sats=900, size=300) pass=['a'] actual_rate=3.00
  ev_decrement=[('b', '900')]
  minfeerate feerate=3 total=fee_sz(sats=900, size=300) pass=['a'] actual_rate=3.00
  ev_decrement=[('b', '2900')]
  minfeerate feerate=3.1 total=fee_sz(sats=0, size=0) pass=[] actual_rate=0.00
  ev_decrement=[('a', '30.00'), ('b', '3030.0')]
  minfeerate feerate=0.1 total=fee_sz(sats=1000, size=1300) pass=['a', 'b'] actual_rate=0.77
  ev_decrement=[]

-------------------------------- graph:
Child C has two parents, A and B
{'a': [], 'b': [], 'c': ['a', 'b']}

https://github.com/bitcoin/bitcoin/pull/22290#issuecomment-865208890
Consider a 3 transaction package where one child transaction C has two
parents, A and B, all of equal size. Suppose A and B are zero-fee transactions
and C has a fee rate of 2. Then each of A and B would evaluate to having
a fee rate of 1 (with C), but as a package the fee rate would be just 2/3.
If the mempool min fee or min relay fee is 1, then this package would make it
in despite being below the required fee rate.
{'a': (0, 100), 'b': (0, 100), 'c': (2, 100)}
  minfeerate feerate=1 total=fee_sz(sats=0, size=0) pass=[] actual_rate=0.00
  ev_decrement=[('a', '100'), ('b', '100'), ('c', '298')]
  minfeerate feerate=0.6 total=fee_sz(sats=0, size=0) pass=[] actual_rate=0.00
  ev_decrement=[('a', '60.000'), ('b', '60.000'), ('c', '178.00')]

-------------------------------- graph:
This is an arbitrary nontrivial graph
{'a': [], 'b': [], 'c': ['a', 'b'], 'd': [], 'e': ['a', 'c'], 'f': ['a'], 'g': ['d', 'e'], 'h': ['e', 'f']}

G has a very high fee, can pull all its (low feerate) ancestors along
{'a': (100, 500), 'b': (200, 400), 'c': (200, 600), 'd': (100, 800), 'e': (0, 300), 'f': (300, 900), 'g': (8000, 400), 'h': (600, 600)}
  minfeerate feerate=2 total=fee_sz(sats=8600, size=3000) pass=['a', 'b', 'c', 'd', 'e', 'g'] actual_rate=2.87
  ev_decrement=[('f', '1500'), ('h', '2100')]
  minfeerate feerate=0.2 total=fee_sz(sats=9500, size=4500) pass=['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'] actual_rate=2.11
  ev_decrement=[]

high fee E pulls in its (low feerate) ancestors, but E's decendants are too low at min_feerate=2
{'a': (100, 500), 'b': (200, 400), 'c': (200, 600), 'd': (100, 800), 'e': (6000, 300), 'f': (300, 900), 'g': (400, 400), 'h': (600, 600)}
  minfeerate feerate=2 total=fee_sz(sats=6500, size=1800) pass=['a', 'b', 'c', 'e'] actual_rate=3.61
  ev_decrement=[('d', '1500'), ('f', '1500'), ('g', '1900'), ('h', '2100')]
  minfeerate feerate=0.5 total=fee_sz(sats=7400, size=3300) pass=['a', 'b', 'c', 'e', 'f', 'h'] actual_rate=2.24
  ev_decrement=[('d', '300.0'), ('g', '100.0')]

-------------------------------- graph:
This graph shows why multiple passes may be needed (the 'progress' variable)
{'a': [], 'b': ['a'], 'c': ['a']}

[A, B] feerate (900/1000) (evaluated first) is not quite large enough, but [A, C] (1100/1000) is
{'a': (100, 500), 'b': (800, 500), 'c': (1000, 500)}
  minfeerate feerate=1 total=fee_sz(sats=1900, size=1500) pass=['a', 'b', 'c'] actual_rate=1.27
  ev_decrement=[]

Same but reverse B and C, first evaluate what was [] previously
{'a': (100, 500), 'b': (1000, 500), 'c': (800, 500)}
  minfeerate feerate=1 total=fee_sz(sats=1900, size=1500) pass=['a', 'b', 'c'] actual_rate=1.27
  ev_decrement=[]

-------------------------------- graph:
https://gist.github.com/glozow/dc4e9d5c5b14ade7cdfac40f43adb18a#packages-are-multi-parent-1-child example D
{'a': [], 'b': ['a'], 'c': ['a'], 'd': ['a', 'b', 'c']}

B and C are children of A and parents of D; A is also a parent of D
{'a': (100, 500), 'b': (800, 500), 'c': (1000, 500), 'd': (100, 1000)}
  minfeerate feerate=1 total=fee_sz(sats=1900, size=1500) pass=['a', 'b', 'c'] actual_rate=1.27
  ev_decrement=[('d', '900')]
  minfeerate feerate=0.1 total=fee_sz(sats=2000, size=2500) pass=['a', 'b', 'c', 'd'] actual_rate=0.80
  ev_decrement=[]
  minfeerate feerate=0.8 total=fee_sz(sats=1900, size=1500) pass=['a', 'b', 'c'] actual_rate=1.27
  ev_decrement=[('d', '700.00')]
```
