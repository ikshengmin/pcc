# ZXing 3.5.4 Reed-Solomon: unchanged Java package target

Selected by docs/python-native-extension-languages.md as the staged Java
acceptance candidate: first the bounded Reed-Solomon source package, then a QR
decoding/API subset, then a new PCC Python binding. This directory is only the
first stage; it is not a ZXing, pyzxing or Python-binding claim.

The source lock pins official ZXing release `zxing-3.5.4` (annotated tag object
`3116a1970d66973475352d8161f2c42bf0a0ea7f`) at commit
`f651b0a0375676e47144f73397dddff8868b0e4c`, read from immutable
raw.githubusercontent.com URLs and checked against the GitHub tree's Git blob
IDs. It retains all five files of `com.google.zxing.common.reedsolomon`
(`GenericGF`, `GenericGFPoly`, `ReedSolomonDecoder`, `ReedSolomonEncoder`,
`ReedSolomonException`), both original upstream test classes of that package
(`GenericGFPolyTestCase`, `ReedSolomonTestCase`), and root LICENSE, NOTICE,
AUTHORS, README.md, pom.xml plus core/pom.xml. Nothing is stripped or adapted;
the package itself depends only on java.lang and java.util. License: Apache-2.0.

The upstream tests use JUnit 4.13.2 (EPL-1.0) and hamcrest-core 1.3
(BSD-3-Clause), declared in the root pom.xml. No verified JUnit source or binary
closure is supplied, so owned and reference upstream-test execution remain
explicitly UNRUN; the lock records that missing dependency closure. No Maven,
network download, JAR/class-file production fallback or workflow change exists.

`tests/java/test_source_zxing_reedsolomon_project.py` drives the work:

- ordinary nodes verify every original byte against the lock;
- the integration node compiles all five original production units with the
  owned Java source frontend and requires success. A compiler rejection is a
  failing test, never an expected-gap pass;
- with `PCC_JAVA_RAW_REFERENCE` (a Linux JDK 9+ `java`), a separate reference
  node compiles the same originals with `javac --release 8`. It cannot supply
  PCC output or qualify PCC.

First observed blocker (local Mac, PCC master at the native-array round): both
the native compiler and the class model stopped at `GenericGF.java:162:3`, the
`@Override` annotation on `toString` (`java.unsupported.annotations`). With
marker `@Override` parsed and checked against Object's methods, the native
compiler stops at `GenericGF.java:33:23` (a static field of class type,
`java.unsupported.declared-result-type`) and the class model at
`GenericGF.java:42:17` (final instance fields,
`java.unsupported.final-instance-fields`).
