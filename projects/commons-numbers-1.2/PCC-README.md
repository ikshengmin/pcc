# Commons Numbers Primes 1.2: unchanged project target

The source lock retains all 12 files of the original primes module at Apache
commit `77f715769e4039acb4babd9add80b78a06ca7279`, plus its root POM, LICENSE,
NOTICE, README and release notes. All three original test classes are inputs:
`PrimesTest`, `SmallPrimesTest` and `UserGuideTest`. Nothing is stripped or adapted.

The CI-only entrypoint is `scripts/ci_java_primes_project.py --output <outside-repository-directory>`.
It reuses the existing 90-second/1-GiB process-tree guard, verifies every original
byte before and after work, and retains raw evidence outside Git. The first PCC
stage supplies all three production units to the public source compiler. Only
after it succeeds does the next stage supply production plus every original test
class. Selecting the `isPrime(I)Z` export does not omit other methods or establish
execution. Missing dependencies are never synthesized or replaced with JARs.

The existing Java CI source lane also selects
`tests/java/test_source_commons_numbers_project.py::test_original_primes_whole_project_owned_source_frontier`.
This integration node invokes the actual worker under CI's existing process-tree
guard and requires every owned stage to pass. A compiler rejection is a failing
test, not a passing expected-gap assertion. Contract tests are separate ordinary
nodes. The integration node cannot use the optional external JDK reference.

Setting the existing `PCC_JAVA_RAW_REFERENCE` to an explicit Linux JDK9+ `java`
executable enables a separate `javac --release 8` production-source reference.
It cannot supply PCC output or qualify PCC. JUnit reference compilation/execution
remain explicitly UNRUN: no verified JUnit artifact/dependency closure is supplied.
No Maven, network download, JAR/class-file production fallback or workflow change
is introduced. Both owned and reference test-execution stages remain incomplete.

Dependency provenance pins Commons Parent 72 (Apache parent 33), JUnit Jupiter
5.11.0-M2 / Platform 1.11.0-M2, API Guardian 1.1.2 and OpenTest4J 1.3.0 with
official source revisions and license identities. This metadata is **not** the
complete licensed dependency source/build closure. The lock enumerates what is
missing. The runner always returns nonzero while that closure remains missing.

Current first-blocker context is separate from new execution: retained PCC db5bfa94
actually rejected the string literal at `Primes.java:31:44`; source inspection
of a49230d8 finds the same lexical boundary. No outcome is prefilled from that
history. Later experimental array-frontier results do not qualify current master.
Runtime-bearing work awaits the shared managed-object contract, not a Java-only
runtime. All new harness, compiler and reference execution is **UNRUN pending CI**.
