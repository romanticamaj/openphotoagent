"""Near-duplicate clustering (05 §2.2; 09b SRCH-DUP-004): union-find over pHash / embedding neighbours."""

import random

import pytest
from opagent_search.dedup import cosine, group_near, hamming


def brute(items, max_ham, min_cos):
    n = len(items)
    adj = {i: set() for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            a, b = items[i], items[j]
            near = hamming(a[1], b[1]) <= max_ham or (a[2] and b[2] and cosine(a[2], b[2]) >= min_cos)
            if near:
                adj[i].add(j)
                adj[j].add(i)
    seen, groups = set(), []
    for i in range(n):
        if i in seen or not adj[i]:
            continue
        stack, comp = [i], set()
        while stack:
            k = stack.pop()
            if k in comp:
                continue
            comp.add(k)
            stack.extend(adj[k] - comp)
        seen |= comp
        groups.append(sorted(items[k][0] for k in comp))
    return sorted(groups)


@pytest.mark.unit
@pytest.mark.tc("SRCH-DUP-004")
def test_dup_004_union_find_equals_brute_force_transitive_closure():
    rnd = random.Random(7)
    items = []
    for c in range(120):  # 120 planted clusters of 1–5 near copies, plus singletons
        base = rnd.getrandbits(64)
        for k in range(rnd.randint(1, 5)):
            flipped = base
            for _ in range(rnd.randint(0, 5)):
                flipped ^= 1 << rnd.randrange(64)
            items.append((f"c{c}-{k}", f"{flipped:016x}", None))
    while len(items) < 1000:
        items.append((f"s{len(items)}", f"{rnd.getrandbits(64):016x}", None))
    rnd.shuffle(items)
    got = sorted(sorted(g) for g in group_near(items, max_hamming=6, min_cosine=0.97))
    assert got == brute(items, 6, 0.97)
    assert any(len(g) >= 3 for g in got)  # transitive chains are joined


@pytest.mark.unit
@pytest.mark.tc("SRCH-DUP-004")
def test_dup_004_embedding_neighbours_join_too():
    items = [
        ("a", "0000000000000000", [1.0, 0.0]),
        ("b", "ffffffffffffffff", [0.99, 0.05]),
        ("c", "00000000ffffffff", [0.0, 1.0]),
    ]
    assert group_near(items, max_hamming=6, min_cosine=0.97) == [["a", "b"]]
