"""Pinned OpenJDK jdk8u512-ga API sources imported by Commons Numbers Primes.

projects/openjdk-jdk8u512-ga (loader scripts/java_jdk8u_sources.py) pins,
unchanged, the Java SE 8 sources of every java.* type that the Primes
production and upstream test units import directly, with the OpenJDK
LICENSE and ASSEMBLY_EXCEPTION. Supplied to the class model next to the
production units, they replace the missing-source boundary with the first
gap inside the JDK code itself.
"""
import re
import shutil

import pytest

from pcc.frontends.java import UnsupportedJava, analyze_class_sources
from scripts.java_jdk8u_sources import FIXTURE, load_jdk_sources
from tests.java.test_source_integral_arithmetic_model import PRODUCTION, TESTS, load_inputs, original_sources


def test_pin_covers_exactly_the_direct_jdk_imports():
    lock, jdk = load_jdk_sources()
    _, files = load_inputs()
    imported = {name for unit in PRODUCTION + TESTS
                for name in re.findall(r'(?m)^import (java\.[\w.]+);', files[unit].decode())}
    assert imported == set(lock['imported_types'])
    assert set(jdk) == set(lock['imported_types'].values())
    assert all('subject to the "Classpath" exception' in text for text in jdk.values())


def test_a_changed_byte_is_rejected(tmp_path):
    copy = tmp_path / 'jdk'
    shutil.copytree(FIXTURE, copy)
    target = copy / 'jdk/src/share/classes/java/util/List.java'
    target.write_bytes(target.read_bytes().replace(b'public interface List', b'public interface Lisd', 1))
    with pytest.raises(ValueError, match='checksum/size/bound mismatch'):
        load_jdk_sources(copy)


def test_primes_with_the_pinned_jdk_sources_stop_inside_the_jdk_code():
    _, files = load_inputs()
    _, jdk = load_jdk_sources()
    sources = dict(original_sources(files, PRODUCTION))
    sources.update(jdk)
    # Formerly the missing-source boundary for java.util.List, then
    # BigInteger's supertypes at 125:25 (tests/java/test_source_class_supertypes.py);
    # the class model now parses that header and stops at the final instance
    # field `final int signum;`.
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources(sources)
    assert caught.value.code == 'java.unsupported.final-instance-fields'
    assert str(caught.value).startswith('jdk/src/share/classes/java/math/BigInteger.java:134:11:')
