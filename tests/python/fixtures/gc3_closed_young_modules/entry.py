"""Ordinary managed imports with sibling globals, owners and exception roots.

Python reference: 3.15 language reference 5 and 7.11 (import and globals).
The same sources run under CPython and PCC's static executable pipeline.
"""
from young_values import exercise, saved, holder

print(exercise())
print(saved)
print(holder.value)
