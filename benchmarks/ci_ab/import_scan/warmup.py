"""Warm both scanner branches and the common native URL provider."""

from urllib.parse import quote


def warmup():
    return 1 + 2 + 3


print(warmup())
print(quote("a b/c", safe=""))
try:
    raise AttributeError("exercise the fallback import at runtime")
except AttributeError:
    from urllib.parse import quote as fallback_quote
    print(fallback_quote("d e/f", safe=""))
