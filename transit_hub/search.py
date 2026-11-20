def first_at_or_after(values, target):
    """Index of the first element >= target in a sorted sequence, or len(values)."""
    lo, hi = 0, len(values)
    while lo < hi:
        mid = (lo + hi) >> 1
        if values[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo
