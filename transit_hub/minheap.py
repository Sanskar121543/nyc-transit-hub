class MinHeap:
    """Binary min heap of (key, value) pairs.

    Ties on key are broken by insertion order, so values are never compared.
    """

    __slots__ = ("_items", "_seq")

    def __init__(self):
        self._items = []
        self._seq = 0

    def __len__(self):
        return len(self._items)

    def __bool__(self):
        return bool(self._items)

    def push(self, key, value):
        self._seq += 1
        items = self._items
        entry = (key, self._seq, value)
        items.append(entry)
        i = len(items) - 1
        while i:
            parent = (i - 1) >> 1
            if items[parent] <= entry:
                break
            items[i] = items[parent]
            i = parent
        items[i] = entry

    def peek(self):
        if not self._items:
            raise IndexError("peek from empty heap")
        top = self._items[0]
        return top[0], top[2]

    def pop(self):
        items = self._items
        if not items:
            raise IndexError("pop from empty heap")
        top = items[0]
        last = items.pop()
        if items:
            n = len(items)
            i = 0
            while True:
                child = 2 * i + 1
                if child >= n:
                    break
                right = child + 1
                if right < n and items[right] < items[child]:
                    child = right
                if items[child] >= last:
                    break
                items[i] = items[child]
                i = child
            items[i] = last
        return top[0], top[2]
