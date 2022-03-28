# bitcoin_package_accept_demo

## Summary

Demonstrate one possible bitcoin mempool package minimum fee
acceptance algorithm.

This repository grew out of my review of Bitcoin Core
[PR 24152](https://github.com/bitcoin/bitcoin/pull/24152).

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

## Algorithm requirements

The algorithm takes a package and a minimum fee rate as inputs
and generates a subset of the package (filters it) so that the result
includes only those transactions and groups of transactions that
satisfy the minimum fee rate. These are the transactions
from the package that should be accepted into the mempool and relayed
to other nodes (assuming all the other validation checks pass). The
algorithm accepts a transaction by including it in a _result list_.

The procedure is trivial if all transactions are independent of each
other: simply calculate each transaction's fee rate (its fee divided
by its size), and if it's greater than or equal to the minimum fee
rate, then add the transaction to the result list.

#### dependencies

But it's tricky when there are dependencies. If a package has
two transactions, A and its child B (B has an input that refers to
one of A's outputs), there are three possible results:

- []
- [A]
- [A, B]

If neither transaction has a sufficiently high fee rate, the
result will be the empty list. 
If only A has the minimum fee rate, we can take it alone.
We can't take only B as it would be
an invalid transaction (it would have a nonexistent input).
We can take both if the _overall_ fee rate (the sum of the two feess
divided by the sum of the two sizes) is at least the minimum fee rate.

#### miner-incentive compatible

An additional requirement, which is somewhat subtle, is that the result
must be the most miner-incentive (miner-revenue maximizing) possible.
I haven't proven that this algorithm accomplishes that, but experiments
seem to show that it is. Here's an example:
Suppose A has a sufficient
fee rate by itself, and B does not, but the combination of A and B
has a sufficient feerate. Accepting the package [A, B]
would satisfy the minimum fee rate. However, doing so would implement a
[parent-pays-for-child](https://gist.github.com/glozow/dc4e9d5c5b14ade7cdfac40f43adb18a#always-try-individual-submission-first)
policy, which is not miner-incentive compatible.
The reason is that taking the
parent, A, by itself has an even better feerate,
and B is not needed to mine A. So the algorithm should select only A.

## Algorithm details (Python script)

The algorithm, `filter_package()` iterates through the package
transactions (graph nodes) sequentially
(in topologically sorted order), left-to-right (or top to bottom).
We calculate its `ancestor_fee` and
`ancestor_size` by adding its own values of those to those of
its parents, if any.
The results are stored in the current node for the benefit of
the nodes to follow. (This is why it's not necessary to
recursively visit parents of parents; our parents will already
have their values set.)
If the result, `ancestor_fee / ancestor_size`, is at least the
minimum fee rate argument, then we add this node and all its
ancestors to the `result` set (they should be added to the mempool
and relayed). We do this using the common graph-traversal
technique of the "todo list" which we work on until it's empty;
this obviates the need for actual recursion.

A very important point is that these nodes that we're
adding to the `result` set should _no longer_ be
considered part of the (input) graph. This is because they
no longer need to be "paid for" by descendants.
Python doesn't allow you
to delete an item from a dictionary while you're iterating it,
so we don't remove these nodes. But a node's presence in the
`result` set _logically_ makes it no longer part of the graph
that we're traversing. Thus you see the checks for a node
being `in result` and skipped if so.

The first version of this algorithm made a single pass through
the package, but then I discovered a problem, which is demonstrated
by the test case "This graph shows why multiple passes may be
needed (the 'progress' variable)". Transaction A has two
children, B and C. A has a low fee rate, and either B or
C may be able to pay for parent A. First, the algorithm
skips A (doesn't add it to `result`) since it doesn't pass the
feerate test. Now we look
at B. Its feerate is sufficient by itself, but since it has a parent A,
we must calculate the feerate of the
two together, which barely doesn't pass the test. So they
remain (are not added to `result`). Now we evaluate C. It
has a higher fee rate than B, high enough that C and A
are included in the result. We're left with just B, which
_by itself_ has a suffient feerate, but we didn't include it.

Notice that if we had visited C then B, which would still
conform to topological sort order, all three transactions
would have been included -- which is clearly the desired
outcome.

The easist way to fix this seems to be to make multiple
passes over the input. The first pass moves A and C to
the result, the second pass moves B (since it no longer
has a parent to pay for). We keep looping
until we make no progress, a common technique in
dynamic programming.

A requirement to
do multiple passes isn't great, and is a possible DoS
concern, but I can't imagine this would be a problem in
practice. Even if the graph has the maximum number of 25
transactions, the first pass should pick up almost
all that should be included, leaving just a very few for
a second (or subsequent) pass. The passes become faster
too, since fewer nodes remain each time. (Technically,
there are the same number of nodes since we can't modify
the input dictionary while iterating it, but we can skip
many of them very quickly since they're in the result.)

#### test structure

The test is a series of graphs (DAGs), each of which has
multiple per-node fee and size assignments, and each of
those in turn can be evaluated at various minimum fee rates.
It should be pretty simple to add new test cases.

## Output

Here's the current output of the demo program:
```
-------------------------------- graph:
Simplest possible graph, a single node
{'a': []}

trivial case, a single node, feerate is individually calculated
{'a': (40, 10)}
   result=[] min_feerate=5 total_fee=0 total_size=0 actual_rate=0.00
   result=['a'] min_feerate=4 total_fee=40 total_size=10 actual_rate=4.00
   result=['a'] min_feerate=1 total_fee=40 total_size=10 actual_rate=4.00

-------------------------------- graph:
Simple two-transaction parent-child case
{'a': [], 'b': ['a']}

child pays for parent: low-fee parent A, high-fee child B
{'a': (1, 3), 'b': (7, 1)}
   result=['a', 'b'] min_feerate=2 total_fee=8 total_size=4 actual_rate=2.00
   result=[] min_feerate=2.1 total_fee=0 total_size=0 actual_rate=0.00

unsuccessful parent pays for child: high-fee parent A, low-fee child B
{'a': (9, 3), 'b': (1, 10)}
   result=['a'] min_feerate=1 total_fee=9 total_size=3 actual_rate=3.00
   result=['a'] min_feerate=3 total_fee=9 total_size=3 actual_rate=3.00
   result=[] min_feerate=3.1 total_fee=0 total_size=0 actual_rate=0.00
   result=['a', 'b'] min_feerate=0.1 total_fee=10 total_size=13 actual_rate=0.77

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
{'a': (0, 1), 'b': (0, 1), 'c': (2, 1)}
   result=[] min_feerate=1 total_fee=0 total_size=0 actual_rate=0.00
   result=['a', 'b', 'c'] min_feerate=0.6 total_fee=2 total_size=3 actual_rate=0.67

-------------------------------- graph:
This is an arbitrary nontrivial graph
{'a': [], 'b': [], 'c': ['a', 'b'], 'd': [], 'e': ['a', 'c'], 'f': ['a'], 'g': ['d', 'e'], 'h': ['e', 'f']}

G has a very high fee, can pull all its (low feerate) ancestors along
{'a': (1, 5), 'b': (2, 4), 'c': (2, 6), 'd': (1, 8), 'e': (0, 3), 'f': (3, 9), 'g': (80, 4), 'h': (6, 6)}
   result=['a', 'b', 'c', 'd', 'e', 'g'] min_feerate=2 total_fee=86 total_size=30 actual_rate=2.87
   result=['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'] min_feerate=0.2 total_fee=95 total_size=45 actual_rate=2.11

high fee E pulls in its (low feerate) ancestors, but E's decendants are too low at min_feerate=2
{'a': (1, 5), 'b': (2, 4), 'c': (2, 6), 'd': (1, 8), 'e': (60, 3), 'f': (3, 9), 'g': (4, 4), 'h': (6, 6)}
   result=['a', 'b', 'c', 'e'] min_feerate=2 total_fee=65 total_size=18 actual_rate=3.61
   result=['a', 'b', 'c', 'e', 'f', 'h'] min_feerate=0.5 total_fee=74 total_size=33 actual_rate=2.24

-------------------------------- graph:
This graph shows why multiple passes may be needed (the 'progress' variable)
{'a': [], 'b': ['a'], 'c': ['a']}

[A, B] feerate (9/10) (evaluated first) is not quite large enough, but [A, C] (11/10) is
{'a': (1, 5), 'b': (8, 5), 'c': (10, 5)}
   result=['a', 'b', 'c'] min_feerate=1 total_fee=19 total_size=15 actual_rate=1.27

Same but reverse B and C, first evaluate what was [] previously
{'a': (1, 5), 'b': (10, 5), 'c': (8, 5)}
   result=['a', 'b', 'c'] min_feerate=1 total_fee=19 total_size=15 actual_rate=1.27

-------------------------------- graph:
https://gist.github.com/glozow/dc4e9d5c5b14ade7cdfac40f43adb18a#packages-are-multi-parent-1-child example D
{'a': [], 'b': ['a'], 'c': ['a'], 'd': ['a', 'b', 'c']}

B and C are children of A and parents of D; A is also a parent of D
{'a': (1, 5), 'b': (8, 5), 'c': (10, 5), 'd': (1, 10)}
   result=['a', 'b', 'c'] min_feerate=1 total_fee=19 total_size=15 actual_rate=1.27
   result=['a', 'b', 'c', 'd'] min_feerate=0.1 total_fee=20 total_size=25 actual_rate=0.80
   result=['a', 'b', 'c'] min_feerate=0.8 total_fee=19 total_size=15 actual_rate=1.27
```