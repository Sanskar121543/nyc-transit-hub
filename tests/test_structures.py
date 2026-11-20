import bisect
import heapq
import random
import unittest
from array import array

from transit_hub.minheap import MinHeap
from transit_hub.search import first_at_or_after


class MinHeapTests(unittest.TestCase):
    def test_pops_in_sorted_order(self):
        rng = random.Random(1)
        values = [rng.randint(0, 50) for _ in range(500)]
        heap = MinHeap()
        for value in values:
            heap.push(value, value)
        popped = [heap.pop()[0] for _ in range(len(values))]
        self.assertEqual(popped, sorted(values))
        self.assertFalse(heap)

    def test_equal_keys_come_out_in_insertion_order(self):
        heap = MinHeap()
        for name in "abcde":
            heap.push(1, name)
        self.assertEqual([heap.pop()[1] for _ in range(5)], list("abcde"))

    def test_values_are_never_compared(self):
        heap = MinHeap()
        heap.push(1, {"a": 1})
        heap.push(1, {"b": 2})
        heap.push(1, object())
        self.assertEqual(len(heap), 3)
        heap.pop()
        heap.pop()
        heap.pop()

    def test_peek_does_not_remove(self):
        heap = MinHeap()
        heap.push(5, "five")
        heap.push(2, "two")
        self.assertEqual(heap.peek(), (2, "two"))
        self.assertEqual(len(heap), 2)

    def test_empty_heap_raises(self):
        heap = MinHeap()
        with self.assertRaises(IndexError):
            heap.pop()
        with self.assertRaises(IndexError):
            heap.peek()

    def test_matches_heapq_under_interleaved_operations(self):
        rng = random.Random(2)
        ours, reference = MinHeap(), []
        for step in range(2000):
            if reference and rng.random() < 0.45:
                self.assertEqual(ours.pop()[0], heapq.heappop(reference)[0])
            else:
                key = rng.randint(0, 100)
                ours.push(key, step)
                heapq.heappush(reference, (key, step))
            self.assertEqual(len(ours), len(reference))

    def test_stale_entries_can_be_skipped_like_the_router_does(self):
        heap = MinHeap()
        best = {"D": 8}
        heap.push(8, "D")
        best["D"] = 6
        heap.push(6, "D")
        settled = []
        while heap:
            distance, node = heap.pop()
            if distance > best[node]:
                continue
            settled.append((node, distance))
        self.assertEqual(settled, [("D", 6)])


class BinarySearchTests(unittest.TestCase):
    def test_matches_bisect_on_random_data(self):
        rng = random.Random(3)
        for _ in range(300):
            values = sorted(rng.randint(0, 40) for _ in range(rng.randint(0, 25)))
            for target in range(-2, 45):
                self.assertEqual(first_at_or_after(values, target), bisect.bisect_left(values, target))

    def test_works_on_typed_arrays(self):
        values = array("i", [10, 20, 20, 30])
        self.assertEqual(first_at_or_after(values, 20), 1)
        self.assertEqual(first_at_or_after(values, 21), 3)
        self.assertEqual(first_at_or_after(values, 31), 4)

    def test_empty_sequence(self):
        self.assertEqual(first_at_or_after([], 5), 0)


if __name__ == "__main__":
    unittest.main()
