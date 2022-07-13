#!/bin/env python3
#
# Given a package of transactions, in the form of an arbitrary DAG, and a
# minimum fee rate, calculate a subset package that satisfies the fee requirement.

test_cases = (
    (
        "Simplest possible graph, a single node",
        {
            'a': [],
        },
        [   # Each case is a description, (fee,size) assignments, and min fee rates:
            ("trivial case, a single node, feerate is individually calculated",
            {'a':(40,10)},
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
            {'a':(1,3), 'b':(7,1)},
            [2, 2.1]),

            ("unsuccessful parent pays for child: high-fee parent A, low-fee child B",
            {'a':(9,3), 'b':(1,10)},
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
            {'a':(0,1), 'b':(0,1), 'c':(2,1)},
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
            {'a':(1,5), 'b':(2,4), 'c':(2,6), 'd':(1,8), 'e':(0,3), 'f':(3,9), 'g':(80,4), 'h':(6,6)},
            [2, 0.2]),

            ("high fee E pulls in its (low feerate) ancestors, but E's decendants are too low at min_feerate=2",
            {'a':(1,5), 'b':(2,4), 'c':(2,6), 'd':(1,8), 'e':(60,3), 'f':(3,9), 'g':(4,4), 'h':(6,6)},
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
            ("[A, B] feerate (9/10) (evaluated first) is not quite large enough, but [A, C] (11/10) is",
            {'a':(1,5), 'b':(8,5), 'c':(10,5)},
            [1]),

            ("Same but reverse B and C, first evaluate what was [] previously",
            {'a':(1,5), 'b':(10,5), 'c':(8,5)},
            [1]),
        ]
    ),
    (
        "https://gist.github.com/glozow/dc4e9d5c5b14ade7cdfac40f43adb18a#packages-are-multi-parent-1-child" +
        " example D",
        {
            'a': [],
            'b': ['a'],
            'c': ['a'],
            'd': ['a', 'b', 'c']
        },
        [   # Each case is a description, (fee,size) assignments, and min fee rates:
            ("B and C are children of A and parents of D; A is also a parent of D",
            {'a':(1,5), 'b':(8,5), 'c':(10,5), 'd':(1,10)},
            [1, 0.1, 0.8]),
        ]
    ),
)

# return a subset of the transaction package that passes the fee rate test
def filter_package(graph, fees_sizes, min_feerate):
    result = set()
    # ancestor_fees_sizes includes the transaction itself plus its ancestors
    ancestor_fees_sizes = {}
    progress = True
    while (progress):
        progress = False
        for txid in graph:
            # no need to evaluate already-accepted tx
            if txid in result: continue
            (ancestor_fee, ancestor_size) = fees_sizes[txid]
            # add the fees and sizes of our parents' ancestors' fees and sizes
            for parent in graph[txid]:
                if not parent in result:
                    ancestor_fee += ancestor_fees_sizes[parent][0]
                    ancestor_size += ancestor_fees_sizes[parent][1]
            ancestor_fees_sizes[txid] = (ancestor_fee, ancestor_size)
            if ancestor_fee / ancestor_size >= min_feerate:
                # feerate is good enough, move this tx and its ancestors to result
                todo = [txid]
                while len(todo) > 0:
                    t = todo.pop(0)
                    if t in result: continue
                    progress = True
                    result.add(t)
                    for parent in graph[t]:
                        todo.append(parent)
    return sorted(list(result))

def test_package():
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
            print()
            print(description)
            print(fees_sizes)
            for min_feerate in feerates:
                result = filter_package(graph, fees_sizes, min_feerate)
                total_fee = 0
                total_size = 0
                for txid in result:
                    total_fee += fees_sizes[txid][0]
                    total_size += fees_sizes[txid][1]
                actual_rate = 'actual_rate={:.2f}'.format(total_fee / total_size if total_size > 0 else 0)
                print('  ', f'{result=} {min_feerate=} {total_fee=} {total_size=}', actual_rate)


if __name__ == '__main__':
    test_package()
