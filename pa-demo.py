#!/bin/env python3
#
# Given a package of transactions, in the form of an arbitrary DAG, and a
# minimum fee rate, calculate a subset package that satisfies the fee requirement.

from decimal import Decimal, getcontext

test_cases = (
    (
        "Simplest possible graph, a single tx",
        {
            'a': [],
        },
        [   # Each case is a description, (fee,size) assignments, and min fee rates:
            ("trivial case, a single tx, feerate is individually calculated",
            {'a':(400,100)},
            [5, 4, 1]),
        ]
    ),
    (
        "Simple two-transaction parent-child case",
        # Each transaction and its parent(s), must be topologically sorted.
        {
            'a': [],
            'b': ['a'],
        },
        [
            ("child pays for parent: low-fee parent A, high-fee child B",
            {'a':(100,300), 'b':(700,100)},
            [2, 2.1]),

            ("unsuccessful parent pays for child: high-fee parent A, low-fee child B",
            {'a':(900,300), 'b':(100,1000)},
            [1, 3, 3.1, 0.1]),
        ]
    ),
    (
        "Child C has two parents, A and B",
        {
            'a': [],
            'b': [],
            'c': ['a', 'b'],
        },
        [
            ("https://github.com/bitcoin/bitcoin/pull/22290#issuecomment-865208890\n" +
            "Consider a 3 transaction package where one child transaction C has two\n" +
            "parents, A and B, all of equal size. Suppose A and B are zero-fee transactions\n" +
            "and C has a fee rate of 2. Then each of A and B would evaluate to having\n" +
            "a fee rate of 1 (with C), but as a package the fee rate would be just 2/3.\n" +
            "If the mempool min fee or min relay fee is 1, then this package would make it\n" +
            "in despite being below the required fee rate.",
            {'a':(0,100), 'b':(0,100), 'c':(2,100)},
            [1, 0.6]),
        ]
    ),
    (
        "This is an arbitrary nontrivial graph",
        #                    ┌───┐
        #                    │   │
        #                    │ F │
        #        ┌───────────┤   ├────┐            ┌───┐
        #        │           │   │    │            │   │
        # ┌───┐  │           └───┘    └────────────┤ H │
        # │   ├──┘                                 │   │
        # │ A │                                ┌───┤   │
        # │   ├─────────────────┐              │   └───┘
        # │   │──┐              │              │
        # └───┘  │              │      ┌───┐   │
        #        │      ┌───┐   │      │   │   │
        #        └──────┤   │   └──────┤ E ├───┘
        #               │ C │          │   │
        #          ┌────┤   ├──────────┤   ├───┐
        #          │    │   │          └───┘   │
        #          │    └───┘                  │   ┌───┐
        #    ┌───┐ │                           │   │   │
        #    │   │ │                           └───┤ G │
        #    │ B ├─┘               ┌───┐           │   │
        #    │   │                 │   ├───────────┤   │
        #    │   │                 │ D │           └───┘
        #    └───┘                 │   │
        #                          │   │
        #                          └───┘
        {
            'a': [],
            'b': [],
            'c': ['a', 'b'],
            'd': [],
            'e': ['a', 'c'],
            'f': ['a'],
            'g': ['d', 'e'],
            'h': ['e', 'f'],
        },
        [
            ("G has a very high fee, can pull all its (low feerate) ancestors along",
            {'a':(100,500), 'b':(200,400), 'c':(200,600), 'd':(100,800), 'e':(0,300), 'f':(300,900), 'g':(8000,400), 'h':(600,600)},
            [2, 0.2]),

            ("high fee E pulls in its (low feerate) ancestors, but E's decendants are too low at min_feerate=2",
            {'a':(100,500), 'b':(200,400), 'c':(200,600), 'd':(100,800), 'e':(6000,300), 'f':(300,900), 'g':(400,400), 'h':(600,600)},
            [2, 0.5]),
        ]
    ),
    (
        "This graph shows why multiple passes may be needed (the 'progress' variable)",
        {
            'a': [],
            'b': ['a'],
            'c': ['a'],
        },
        [   # Each case is a description, (fee,size) assignments, and min fee rates:
            ("[A, B] feerate (900/1000) (evaluated first) is not quite large enough, but [A, C] (1100/1000) is",
            {'a':(100,500), 'b':(800,500), 'c':(1000,500)},
            [1]),

            ("Same but reverse B and C, first evaluate what was [] previously",
            {'a':(100,500), 'b':(1000,500), 'c':(800,500)},
            [1]),
        ]
    ),
    (
        "https://gist.github.com/glozow/dc4e9d5c5b14ade7cdfac40f43adb18a#packages-are-multi-parent-1-child example D",
        {
            'a': [],
            'b': ['a'],
            'c': ['a'],
            'd': ['a', 'b', 'c']
        },
        [   # Each case is a description, (fee,size) assignments, and min fee rates:
            ("B and C are children of A and parents of D; A is also a parent of D",
            {'a':(100,500), 'b':(800,500), 'c':(1000,500), 'd':(100,1000)},
            [1, 0.1, 0.8]),
        ]
    ),
)

# A feerate, except keep the fee and the size separate, so that
# feerates can be added.
class fee_sz:
    def __init__(self, sats: Decimal, size: Decimal):
        self.sats = sats
        self.size = size
    def __add__(self, v):
        return fee_sz(self.sats + v.sats, self.size + v.size)
    def __repr__(self):
        return f'fee_sz(sats={self.sats}, size={self.size})'
    def feerate(self):
        return self.sats / self.size

# Separate the transactions in the graph into those with ancestor feerate
# greater than or equal to the given feerate, and less than. Both lists are 
# topologically sorted.
def partition_by_feerate(graph, fees_sizes, feerate):
    high_fee_set = set()
    # ancestor_fees_sizes includes the transaction itself plus its ancestors
    ancestor_fee_szs = {}
    progress = True
    while (progress):
        progress = False
        for txid in graph:
            # no need to evaluate already-accepted tx
            if txid in high_fee_set: continue
            ancestor_fee_sz = fees_sizes[txid]
            # add the fees and sizes of our parents' ancestors' fees and sizes
            for parent in graph[txid]:
                if parent not in high_fee_set:
                    ancestor_fee_sz += ancestor_fee_szs[parent]
            ancestor_fee_szs[txid] = ancestor_fee_sz
            if ancestor_fee_sz.feerate() < feerate: continue

            # feerate is good enough, move this tx and its ancestors to high_fee_set
            todo = [txid]
            while len(todo) > 0:
                t = todo.pop(0)
                if t in high_fee_set: continue
                progress = True
                high_fee_set.add(t)
                for parent in graph[t]:
                    todo.append(parent)
            if progress: break
    result_ge = [txid for txid in graph if txid in high_fee_set]
    result_lt = [(txid, ancestor_fee_szs[txid]) for txid in graph if txid not in high_fee_set]
    return (result_lt, result_ge)

def test_package():
    getcontext().prec = 5
    for test_case in test_cases:
        graph_description = test_case[0]
        graph = test_case[1]
        print()
        print("-------------------------------- graph:")
        print(graph_description)
        print(graph)
        sub_cases = test_case[2]
        for sub_case in sub_cases:
            description = sub_case[0]
            fees_sizes = sub_case[1]
            feerates = sub_case[2]

            fee_szs = {}
            for tx, fs in fees_sizes.items():
                fee_szs[tx] = fee_sz(fs[0], fs[1])

            print()
            print(description)
            print(fees_sizes)
            for feerate in feerates:
                (result_lt, result_ge) = partition_by_feerate(graph, fee_szs, feerate)

                # minfeerate result: transactions that pass (at or above) minfeerate
                total = fee_sz(0, 0)
                for txid in result_ge: total += fee_szs[txid]
                actual_rate = 'actual_rate={:.2f}'.format(total.feerate() if total.size > 0 else 0)
                print(f'  minfeerate {feerate=} {total=} pass={result_ge}', actual_rate)

                # Effective value decrement: how much to reduce the output value of
                # each tx to achieve requested feerate (note, we're not modelling
                # output amounts here); the fee of the transaction we're constructing
                # will increase by this amount (if we use this tx as an input), so
                # this can also be thought of as a fee-bump.
                ev_decrement = []
                for (txid, ancestor_fee_sz) in result_lt:
                    desired_fee = Decimal(feerate) * Decimal(ancestor_fee_sz.size)
                    decr = str(desired_fee - ancestor_fee_sz.sats)
                    ev_decrement.append((txid, decr))
                print(f'  {ev_decrement=}')


if __name__ == '__main__':
    test_package()
