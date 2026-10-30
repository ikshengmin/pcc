"""Host-only prefix hashing contracts; no compiler or native process runs."""

import hashlib
from pathlib import Path
import struct

import pytest

from pcc.backend import macho_codesign as signing
from pcc.backend import macho_spec as spec


PARAMS = dict(identifier=b"pcc-linked", exec_seg_base=0,
              exec_seg_limit=4096, exec_seg_flags=1)


@pytest.mark.parametrize("limit", [0, 16, 4080, 4096, 4112, 8192, 8208])
def test_prefix_matches_public_builder_and_independent_page_hashes(limit):
    prefix = bytes(index % 251 for index in range(limit))
    image = prefix + b"existing signature trailer must not be hashed" * 100
    actual = signing._build_signature_prefix(image, limit, **PARAMS)
    assert actual == signing.build_signature(prefix, **PARAMS)
    fields = struct.unpack_from(signing._CD_HEADER, actual, 20)
    assert fields[8] == limit
    hash_offset = 20 + fields[4]
    count = (limit + 4095) // 4096
    expected = b"".join(hashlib.sha256(prefix[i:i + 4096]).digest()
                        for i in range(0, limit, 4096))
    assert actual[hash_offset:hash_offset + count * 32] == expected
    assert signing._build_signature_prefix(prefix + b"changed trailer", limit,
                                           **PARAMS) == actual


@pytest.mark.parametrize("offset", [0, 4095, 4096, 4111])
def test_signed_page_tampering_changes_rebuilt_signature(offset):
    image = bytes(4112) + b"signature trailer"
    original = signing._build_signature_prefix(image, 4112, **PARAMS)
    changed = bytearray(image)
    changed[offset] = 1
    assert signing._build_signature_prefix(bytes(changed), 4112,
                                           **PARAMS) != original


@pytest.mark.parametrize("limit, message", [
    (-1, "unsigned 32-bit"), (True, "must be an integer"),
    (1.0, "must be an integer"), (1 << 32, "unsigned 32-bit"),
    (32, "outside the image"), (1, "16-byte aligned"),
])
def test_invalid_prefix_bounds_fail_without_hashing(monkeypatch, limit, message):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid bounds reached hashing")
    monkeypatch.setattr(signing.hashlib, "sha256", forbidden)
    with pytest.raises(signing.CodesignError, match=message):
        signing._build_signature_prefix(bytes(16), limit, **PARAMS)


@pytest.mark.parametrize("image", [bytearray(16), memoryview(bytes(16)), "x" * 16])
def test_prefix_requires_immutable_bytes(image):
    with pytest.raises(signing.CodesignError, match="image must be bytes"):
        signing._build_signature_prefix(image, 16, **PARAMS)


def test_prefix_only_slices_pages_from_original_image(monkeypatch):
    image = bytes(8208) + b"old signature" * 100
    slices = []
    real_view = memoryview

    class TrackedView:
        def __init__(self, owner):
            assert owner is image
            self.view = real_view(owner)

        def __getitem__(self, key):
            slices.append((key.start, key.stop))
            return self.view[key]

    monkeypatch.setattr(signing, "memoryview", TrackedView, raising=False)
    actual = signing._build_signature_prefix(image, 8208, **PARAMS)
    assert actual
    assert slices == [(0, 4096), (4096, 8192), (8192, 8208)]


def test_public_validation_precedes_released_view_length():
    view = memoryview(bytes(16))
    view.release()
    params = dict(PARAMS, identifier=b"")
    with pytest.raises(signing.CodesignError, match="must not be empty"):
        signing.build_signature(view, **params)
    with pytest.raises(ValueError, match="released memoryview"):
        signing.build_signature(view, **PARAMS)


def test_public_bytes_subclass_observes_original_length_and_slice_order():
    events = []

    class ObservedBytes(bytes):
        def __len__(self):
            events.append("length")
            return super().__len__()

        def __getitem__(self, key):
            events.append((key.start, key.stop))
            return super().__getitem__(key)

    region = ObservedBytes(bytes(16))
    with pytest.raises(signing.CodesignError, match="must not be empty"):
        signing.build_signature(region, **dict(PARAMS, identifier=b""))
    assert events == []
    signing.build_signature(region, **PARAMS)
    assert events == ["length", (0, 4096)]


@pytest.mark.parametrize("convert", [bytes, bytearray, memoryview])
def test_public_input_families_keep_signature_bytes(convert):
    region = bytes(4112)
    assert signing.build_signature(convert(region), **PARAMS) == \
        signing.build_signature(region, **PARAMS)


def test_pipeline_preserves_independent_signature_comparison():
    source = (Path(__file__).resolve().parents[2] /
              "pcc/frontends/python/pipeline_self_backend_link.py").read_text()
    assert "signature = parse_signature(image)" in source
    assert "expected_signature = _build_signature_prefix(" in source
    assert "image, signature.dataoff, identifier=signature.identifier" in source
    assert "if image[signature.dataoff:] != expected_signature:" in source
    assert "owned linker produced stale signature page hashes" in source


def test_parsed_image_rebuild_detects_signed_payload_tampering():
    limit = 4112
    size = signing._signature_size(limit, PARAMS["identifier"])
    header = struct.pack("<8I", spec.MH_MAGIC_64, spec.CPU_TYPE_ARM64,
                         spec.CPU_SUBTYPE_ARM64_ALL, spec.MH_EXECUTE,
                         1, 16, 0, 0)
    command = struct.pack("<4I", spec.LC_CODE_SIGNATURE, 16, limit, size)
    prefix = (header + command).ljust(limit, b"\0")
    image = prefix + signing.build_signature(prefix, **PARAMS)
    for tampered in (False, True):
        data = image if not tampered else image[:limit - 1] + b"\1" + image[limit:]
        params = signing.parse_signature(data)
        expected = signing._build_signature_prefix(
            data, params.dataoff, identifier=params.identifier,
            exec_seg_base=params.exec_seg_base,
            exec_seg_limit=params.exec_seg_limit,
            exec_seg_flags=params.exec_seg_flags,
        )
        assert (data[params.dataoff:] == expected) is (not tampered)


@pytest.mark.parametrize("change", [
    dict(identifier=b""), dict(identifier=b"a\0b"),
    dict(exec_seg_base=-1), dict(exec_seg_limit=True),
    dict(exec_seg_flags=1 << 64),
])
def test_prefix_keeps_shared_metadata_failures(change):
    params = dict(PARAMS, **change)
    with pytest.raises(signing.CodesignError) as public_error:
        signing.build_signature(bytes(16), **params)
    with pytest.raises(signing.CodesignError) as prefix_error:
        signing._build_signature_prefix(bytes(32), 16, **params)
    assert str(prefix_error.value) == str(public_error.value)
