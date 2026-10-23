"""Tiny host checks of exact label hashing, without compiler execution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import self_backend_verify as verify


def _original_hash(text):
    value = 0
    index = 0
    while index < len(text):
        value = (value * 131 + ord(text[index])) & 1099511627775
        index += 1
    return value


@pytest.mark.parametrize(
    "labels",
    [
        ["", "entry", ".", "..", "trailing."],
        ["branch.0", "branch.00", "branch.01", "branch.1", ".1", "%.1"],
        ["branch.ready", "branch.failed", "branch.-1", "branch.+1"],
        ["λ.一", "λ.二", "e\u0301.1", "é.1", "🌳.٠١"],
        ["scope.inner.ready.1", "scope.inner.ready.2", "scope..ready.3"],
        ["a" * 127 + ".1", "a" * 128 + ".1", "short." + "9" * 160],
        ["AĀ.1", "B}.1", "AĀ.2", "B}.2"],
    ],
    ids=["empty", "leading-zero", "nondecimal", "unicode", "multiple-dot", "long", "collision"],
)
def test_label_hash_matches_original(labels):
    for label in labels + list(reversed(labels)):
        assert verify._stable_text_key(label) == _original_hash(label)


@pytest.mark.parametrize(
    "label", ["", "a", "region.ready.1", "λ🌳.一"],
    ids=["empty", "one-character", "ordinary", "unicode"],
)
def test_exact_string_length_is_read_once(monkeypatch, label):
    length_reads = []
    character_reads = []

    def counted_len(text):
        length_reads.append(text)
        return len(text)

    def counted_ord(character):
        character_reads.append(character)
        return ord(character)

    monkeypatch.setattr(verify, "len", counted_len, raising=False)
    monkeypatch.setattr(verify, "ord", counted_ord, raising=False)
    assert verify._stable_text_key(label) == _original_hash(label)
    # Instrument the production helper's actual global lookup, not a
    # replacement hash or a string subclass that takes the fallback path.
    assert length_reads == [label]
    assert character_reads == list(label)


class _TinyCFG:
    def __init__(self, names, successors):
        self.block_names = names
        self.successors = successors

    def cfg_successor_count(self, block):
        return len(self.successors[block])

    def cfg_successor_id(self, block, edge):
        return self.successors[block][edge]


def test_cfg_edges_hash_collisions_and_duplicate_diagnostics_are_unchanged():
    names = ["AĀ.1", "B}.1", "region.ready.1", "region.ready.end"]
    key = _original_hash(names[0])
    assert key == _original_hash(names[1])
    buckets, predecessors, successors = verify._build_cfg(
        SimpleNamespace(name="tiny"), _TinyCFG(names, [[1, 2, 1], [3], [3], []]),
    )
    assert buckets == {
        key: [0, 1],
        _original_hash(names[2]): [2],
        _original_hash(names[3]): [3],
    }
    assert predecessors == [[], [0], [0], [1, 2]]
    assert successors == [[1, 2], [3], [3], []]
    with pytest.raises(BackendUnavailable) as caught:
        verify._build_cfg(
            SimpleNamespace(name="tiny"),
            _TinyCFG(names + [names[0]], [[], [], [], [], []]),
        )
    assert str(caught.value) == (
        "self IR verifier [terminator] in 'tiny': "
        "duplicate basic block label 'AĀ.1'"
    )


class _ObservedString(str):
    def __new__(cls, text, trace, lengths, fail_length, fail_character):
        value = super().__new__(cls, text)
        value.trace = trace
        value.lengths = lengths
        value.length_reads = 0
        value.fail_length = fail_length
        value.fail_character = fail_character
        return value

    def __len__(self):
        read = self.length_reads
        self.length_reads += 1
        self.trace.append(("len", read))
        if read == self.fail_length:
            raise ValueError("length read failed")
        if self.lengths is not None and read < len(self.lengths):
            return self.lengths[read]
        return super().__len__()

    def __getitem__(self, index):
        self.trace.append(("getitem", index))
        if index == self.fail_character:
            raise ValueError("character read failed")
        return super().__getitem__(index)


@pytest.mark.parametrize(
    ("lengths", "fail_length", "fail_character", "error"),
    [
        (None, None, None, None),
        ((5, 4, 3, 2), None, None, None),
        ((0,), None, None, None),
        (None, 2, None, "length read failed"),
        (None, None, 2, "character read failed"),
    ],
    ids=["ordinary", "changing-length", "zero-length", "length-error", "character-error"],
)
def test_custom_string_reads_mutations_and_exceptions_are_unchanged(
    lengths, fail_length, fail_character, error,
):
    original_trace = []
    candidate_trace = []
    before = _ObservedString("abc.1", original_trace, lengths, fail_length, fail_character)
    after = _ObservedString("abc.1", candidate_trace, lengths, fail_length, fail_character)
    if error is None:
        assert verify._stable_text_key(after) == _original_hash(before)
    else:
        with pytest.raises(ValueError, match=error):
            _original_hash(before)
        with pytest.raises(ValueError, match=error):
            verify._stable_text_key(after)
    assert candidate_trace == original_trace
