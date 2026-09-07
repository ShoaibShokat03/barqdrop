"""Unit checks for the resume range arithmetic."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from barqdrop.resume import merge_ranges, merge_spans, missing_ranges, split_ranges


def check(label, got, want):
    assert got == want, "%s: got %r, want %r" % (label, got, want)
    print("  ok  %s" % label)


def main():
    print("range arithmetic")
    check("merge adjacent", merge_spans([[0, 10], [10, 20]]), [[0, 20]])
    check("merge overlap", merge_spans([[5, 15], [0, 10]]), [[0, 15]])
    check("merge disjoint", merge_spans([[20, 30], [0, 10]]), [[0, 10], [20, 30]])
    check("merge idempotent", merge_spans(merge_spans([[0, 10], [10, 20]])), [[0, 20]])
    check("ranges to spans", merge_ranges([[0, 10], [10, 10]]), [[0, 20]])
    check("missing all", missing_ranges([], 100), [[0, 100]])
    check("missing none", missing_ranges([[0, 100]], 100), [])
    check("missing middle", missing_ranges([[0, 10], [50, 60]], 100),
          [[10, 40], [60, 40]])
    check("missing zero-size", missing_ranges([], 0), [])
    check("split", split_ranges([[0, 25]], 10), [(0, 10), (10, 10), (20, 5)])

    # Randomised: repeated merging must never claim more than was added.
    for _ in range(300):
        size = random.randint(1, 5000)
        spans = []
        added = set()
        for _ in range(random.randint(0, 25)):
            off = random.randint(0, size - 1)
            length = random.randint(1, size - off)
            spans = merge_spans(spans + [[off, off + length]])
            added.update(range(off, off + length))
        covered = set()
        for s, e in spans:
            assert 0 <= s < e <= size, "span out of bounds: %r" % [s, e]
            covered.update(range(s, e))
        assert covered == added, "coverage drift"
        missing = missing_ranges(spans, size)
        gap = set()
        for o, n in missing:
            gap.update(range(o, o + n))
        assert gap == set(range(size)) - added, "missing/done are not complementary"
    print("  ok  300 randomised merge/missing round-trips")
    print("RANGE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
