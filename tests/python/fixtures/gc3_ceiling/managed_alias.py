"""Compare the computed alias-sensitive sum, not a hard-coded success string."""
import sys

def alias(value):
    left = [value]
    right = [left, value]
    left[0] = value + 7
    return right

def total(n):
    score = 0
    i = 0
    while i < n:
        z = alias(i)
        score += z[0][0] * 3 - z[1]
        i += 1
    return score

print(total(int(sys.argv[1])))
