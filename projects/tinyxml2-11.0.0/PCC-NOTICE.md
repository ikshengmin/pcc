# TinyXML2 test inputs

Upstream: <https://github.com/leethomason/tinyxml2>.
Version: **11.0.0**, commit **9148bdf719e997d1f474be6bcc7943881046dba1**.

The files listed in `pcc-fixture.json`'s `sha256` object are copied byte-for-byte
from that revision. The manifest pins both the acquired source archive and
each retained upstream input. No acquisition happens during tests. Original
line endings, UTF-8 BOMs, binary regression inputs, and embedded notices remain
unchanged. The `.gitattributes` file prevents checkout newline conversion.

The retained distribution contains the library, **complete `xmltest.cpp` and
`resources/` tree**, upstream CMake/Make recipes and CMake helpers, the installed
package test recipe, upstream readme, and complete `LICENSE.txt` (Zlib).
Generated website documentation, artwork, unrelated build systems and examples
are not needed by this test executable and are not vendored here.

`resources/dream.xml` also retains its own notice: the text was placed in the
public domain by Moby Lexical Tools; SGML/XML markup is attributed to Jon Bosak,
and the notice permits worldwide copying and distribution. It is not relabeled
as PCC-authored material. Other retained resources remain under the upstream
distribution's license and preserve their original bytes.

`PCC-NOTICE.md`, `.gitattributes`, and `pcc-fixture.json` are PCC integration
metadata, not upstream files. The separately located
`tests/cpp/fixtures/tinyxml2_smoke.cpp` is PCC-authored and **not** an upstream
test. Its smoke fixture is retained independently from `upstream-tests`.

The upstream fixture runs the original `xmltest` main with no arguments, from
a fresh copy of this source/resource tree. Outputs belong in that copy's
`resources/out/`, never in the vendor directory. The harness checks zero exit
and one positive `Pass N, Fail 0` summary. Both raw streams are preserved;
stderr is not required to be empty by the upstream CTest recipe. The
upstream performance timing line is not required to be byte-identical.
This direct-TU harness runs upstream tests but does not certify CMake, package
installation, shared-library builds, or all build configurations.

Adding these inputs does **not** make TinyXML2 supported by PCC. The external
reference and native PCC compilation/execution gates are separate; unsupported
C++ directory discovery, headers, classes and lifetime semantics remain real
failures, never skips, expected failures, or reference-compiler fallbacks.
