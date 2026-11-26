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


@pytest.mark.parametrize("count", [8, 31, 255])
@pytest.mark.parametrize("bad", [None, 0, 2, -7, -10])
def test_host_partial_block_has_exact_summary_and_visit_count(count, bad):
    # A partial block (starting away from a block boundary) used to decode
    # every root in Python. The packed range has the same result and charge,
    # including malformed words and the signed-int32 lower endpoint.
    if validation.sys.byteorder != "little":
        pytest.skip("packed host words require little-endian order")
    words = [-8 * (i + 1) - i % 2 for i in range(count + 1)]
    words[-1] = -(1 << 31)
    if bad is not None:
        words[1 + count // 2] = bad
    payload = struct.pack("<%di" % len(words), *words)
    scalar = object.__new__(validation._Summaries)
    scalar.remaining = count
    expected = scalar._edge_max(payload, 0, 1, count + 1, False)
    packed = object.__new__(validation._Summaries)
    packed.remaining = count
    assert packed._edge_max(payload, 0, 1, count + 1, True) == expected
    assert packed.remaining == scalar.remaining == 0
    packed.remaining = count - 1
    with pytest.raises(wire.PreciseStackMapError, match="work budget exceeded"):
        packed._edge_max(payload, 0, 1, count + 1, True)
    assert packed.remaining == 0


def test_partial_block_avoids_scalar_decodes_without_skipping_word_charge(monkeypatch):
    if validation.sys.byteorder != "little":
        pytest.skip("packed host words require little-endian order")
    packed = object.__new__(validation._Summaries)
    packed.remaining = 16
    payload = struct.pack("<17i", *([-8] * 16 + [-16]))
    def unexpected(*args):
        pytest.fail("packed edge should not decode an individual root")
    monkeypatch.setattr(packed, "_word", unexpected)
    assert packed._edge_max(payload, 0, 1, 17, True) == 16
    assert packed.remaining == 0
