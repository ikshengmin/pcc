"""Runtime stack-map validation summarizes location blocks in C on the host.

_Summaries keeps, per block of 256 locations, the largest frame offset a
location names (_INVALID for a nonnegative or misaligned one). The host
fills those leaves from the packed words per block (max, min and the low
bytes) instead of decoding every word in Python; the per-location loop
stays for other layouts. The leaves and the work charge must be the loop's.
"""
from __future__ import annotations

import random
import struct

import pytest

from pcc.backend import macho_host_runtime_validation as validation
from pcc.backend import precise_stackmap as wire


class _Budget:
    def check_peak(self, *args, **kwargs):
        return None

    def unretained_payload_bytes(self, payload):
        return 0

    def _rounded(self, value):
        return value


def _leaves(words, host, remaining=None):
    payload = b"\0" * 12 + struct.pack("<%di" % len(words), *words)
    summaries = object.__new__(validation._Summaries)
    blocks = (len(words) + validation._BLOCK_LOCATIONS - 1) // validation._BLOCK_LOCATIONS
    leaves = 1 << max(0, (max(1, blocks) - 1).bit_length())
    summaries.remaining = len(words) if remaining is None else remaining
    tree = bytearray(16 * leaves)
    if host:
        assert summaries._fill_leaves_host(payload, 12, len(words), leaves, tree)
    else:
        for index in range(len(words)):
            summaries._visit()
            value = summaries._word(payload, 12, index)
            position = leaves + index // validation._BLOCK_LOCATIONS
            if value > validation._SUMMARY.unpack_from(tree, 8 * position)[0]:
                validation._SUMMARY.pack_into(tree, 8 * position, value)
    return bytes(tree), summaries.remaining


def _word(rng):
    shape = rng.randrange(6)
    if shape == 0:
        return rng.randrange(0, 1 << 20)            # nonnegative: invalid
    if shape == 1:
        return -rng.randrange(1, 1 << 12) * 8 - rng.choice((2, 4, 6))  # misaligned
    return -rng.randrange(1, 1 << 16) * 8 - rng.randrange(2)  # valid, owned bit


@pytest.mark.parametrize("count", [1, 255, 256, 257, 1000, 4096 + 7])
@pytest.mark.parametrize("invalid_share", [0.0, 0.01, 0.3])
def test_host_leaves_equal_the_per_location_loop(count, invalid_share):
    rng = random.Random(count * 7 + int(invalid_share * 100))
    words = []
    for _ in range(count):
        word = _word(rng)
        if rng.random() >= invalid_share:
            word = -rng.randrange(1, 1 << 16) * 8 - rng.randrange(2)
        words.append(word)
    assert _leaves(words, True) == _leaves(words, False)


def test_host_leaves_charge_one_visit_per_location():
    words = [-8] * 300
    with pytest.raises(wire.PreciseStackMapError, match="work budget exceeded"):
        _leaves(words, True, remaining=299)
    with pytest.raises(wire.PreciseStackMapError, match="work budget exceeded"):
        _leaves(words, False, remaining=299)
    assert _leaves(words, True, remaining=300)[1] == _leaves(words, False, remaining=300)[1] == 0
