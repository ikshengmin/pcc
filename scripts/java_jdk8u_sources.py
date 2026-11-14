"""Pinned OpenJDK jdk8u512-ga Java SE 8 API sources for Commons Numbers Primes.

projects/openjdk-jdk8u512-ga/pcc-source-lock.json pins, unchanged, the
sources of every java.* type that the Primes production and upstream test
units import directly (javac --release 8 is their API), with the OpenJDK
LICENSE and ASSEMBLY_EXCEPTION (GPL-2.0-only WITH Classpath-exception-2.0).
Their own dependencies and java.lang are not pinned. This loader verifies
every byte through the shared source-lock check and returns the Java units.
"""
from __future__ import annotations

from pathlib import Path

from scripts.ci_source_frontier_support import load_source_lock

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "projects/openjdk-jdk8u512-ga"
LOCK_SHA256 = "a443f1fcca36793c24db4d5711f85abcd6be7ef3accffa9256b48da9b3b95f9c"
REVISION = "b661ee6ff840d6922aa7424a4d8f24760b21cf26"
LICENSE = "GPL-2.0-only WITH Classpath-exception-2.0"


def load_jdk_sources(directory=FIXTURE):
    """(lock, {repository path: Java source text}) for the pinned API units."""
    lock, files = load_source_lock(directory, LOCK_SHA256)
    if (lock["project"] != "openjdk-jdk8u" or lock["tag"] != "jdk8u512-ga" or lock["revision"] != REVISION
            or lock["license"] != LICENSE or not {"LICENSE", "ASSEMBLY_EXCEPTION"} <= set(files)
            or set(lock["imported_types"].values()) != {name for name in files if name.endswith(".java")}):
        raise ValueError("pinned OpenJDK source identity or selection differs")
    return lock, {name: value.decode("utf-8") for name, value in files.items() if name.endswith(".java")}
