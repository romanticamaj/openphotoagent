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


# ADR-0043: 「重複」 for collapsing in search and for cleanup is a pHash near-copy of the photo kept, never merely a
# look-alike. On the reference library every wrong group came from the cosine rule (different children in one gym at
# cosine 0.95-0.99, pHash 12-40 bits apart); pHash pairs at 1-7 bits were all real copies.
def _h(bits: int, base: int = 0) -> str:
    """A pHash `bits` bits away from `base`."""
    return f"{base ^ ((1 << bits) - 1):016x}"


@pytest.mark.unit
@pytest.mark.tc("SRCH-DUP-006")
def test_dup_006_look_alikes_are_not_duplicates():
    """Seconds apart, same scene, cosine 0.98-0.991, pHash 20-30 bits: two photos, not one."""
    from opagent_search.dedup import group_duplicates

    items = [("kid1", _h(0), [1.0, 0.0]), ("kid2", _h(20), [0.98, 0.199]), ("kid3", _h(30), [0.991, 0.134])]
    assert group_duplicates(items, rank={}) == []


@pytest.mark.unit
@pytest.mark.tc("SRCH-DUP-007")
def test_dup_007_copies_group_around_the_photo_kept_without_chaining():
    """A-B 5 bits and B-C 5 bits but A-C 10: C is not a copy of A, the one kept, and stays out."""
    from opagent_search.dedup import group_duplicates

    a, b, c = 0, (1 << 5) - 1, ((1 << 5) - 1) ^ (((1 << 5) - 1) << 5)
    items = [("A", f"{a:016x}", None), ("B", f"{b:016x}", None), ("C", f"{c:016x}", None)]
    assert group_duplicates(items, rank={"A": 3, "B": 2, "C": 1}) == [["A", "B"]]


@pytest.mark.unit
@pytest.mark.tc("SRCH-DUP-008")
def test_dup_008_a_hash_collision_with_different_content_is_vetoed():
    """Flat images collide in pHash: 4 bits apart but cosine 0.60 means different pictures. Without vectors the hash
    decides; with one vector missing too."""
    from opagent_search.dedup import group_duplicates

    assert group_duplicates([("x", _h(0), [1.0, 0.0]), ("y", _h(4), [0.6, 0.8])], rank={}) == []
    assert group_duplicates([("x", _h(0), None), ("y", _h(3), None)], rank={}) == [["x", "y"]]
    assert group_duplicates([("x", _h(0), [1.0, 0.0]), ("y", _h(3), None)], rank={}) == [["x", "y"]]
    copies = [(f"c{i}", _h(0), [1.0, 0.0]) for i in range(10)]  # the ten identical collages
    assert group_duplicates(copies, rank={}) == [sorted(f"c{i}" for i in range(10))]
