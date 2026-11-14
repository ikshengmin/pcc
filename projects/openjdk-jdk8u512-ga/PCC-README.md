# OpenJDK jdk8u512-ga: Java SE 8 API sources for Commons Numbers Primes

`pcc-source-lock.json` pins, unchanged, the sources at tag `jdk8u512-ga`
(commit `b661ee6ff840d6922aa7424a4d8f24760b21cf26` of
https://github.com/openjdk/jdk8u) of every `java.*` type that the pinned
Commons Numbers Primes production and upstream test units import directly:
`java.math.BigInteger` and `java.util.AbstractMap`, `ArrayList`, `Arrays`,
`Collections`, `HashSet`, `List`, `Map` and `Set`. Primes is built with
`javac --release 8`, so these are the Java SE 8 API it compiles against.

License: GPL-2.0-only WITH Classpath-exception-2.0. The repository `LICENSE`
and `ASSEMBLY_EXCEPTION` are pinned beside the sources, and every source
keeps its original header designating it as subject to the Classpath
exception.

Each file was downloaded from raw.githubusercontent.com at the commit and
its git blob checked against the GitHub contents API at the same commit; the
lock records path, size, sha256, git blob and URL. `scripts/java_jdk8u_sources.py`
verifies every byte through the shared source-lock check.

This is not a complete build closure: the pinned types' own imports and
`java.lang` are not pinned. Nothing here is a JAR, class file or reference
tool input; it is source for PCC's class model.
