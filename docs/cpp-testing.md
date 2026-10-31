# C++ testing: ordinary checks and project execution

The target is complete C++17, not the currently accepted arithmetic/free-function frontend
slice. This harness preserves the same separation as the C tests: ordinary
frontend/contract tests, executed integration programs, then pinned real
projects. A passing small corpus does not establish C++17 conformance.

## Execution owners and commands

Use the repository's `.python-version`, `uv.lock`, original conftest, and
pytest-xdist configuration. `-n` is deliberately not overridden below: these
commands use the normal six workers. Every invocation includes `-x`.

| Selection | What a pass proves | What it does not prove |
| --- | --- | --- |
| Ordinary `tests/cpp` | Parser/Sema/diagnostics; owned object and link contracts; repeated identical IR/object/executable bytes; fixture configuration and source-copy contracts; exact byte comparisons | Program execution or native compiler ownership |
| `test_cpp_host_owned_executable_exact_outcome` | Actual Linux x86-64 code from the host-executed C++ frontend, owned C lowering/object emitter/linker, with exact stdout/stderr/status | A public C++ CLI, pcc1 frontend execution, complete runtime/library, or full C++17 |
| `test_cpp_host_owned_matches_explicit_cpp17_oracle` | The same input TUs agree with an explicitly invoked external C++17 compiler | External compilers being allowed in the target pipeline |
| `test_cpp_tinyxml2_explicit_cpp17_reference_oracle` | The pinned project and PCC-authored driver build/run with the external reference | TinyXML2 working with PCC |
| `test_cpp_tinyxml2_native_linux_windows_project_exact_outcome` | Supplied Linux/Windows native compiler actually compiles the directory through the public separate-TU route, produces an owned executable and executes the project | A new-source bootstrap fixed point or all upstream TinyXML2 tests |
| `test_cpp_tinyxml2_upstream_tests_explicit_cpp17_reference_oracle` | Unmodified upstream `xmltest.cpp` and library build with the external reference, then the whole runner succeeds twice with its resources | Any PCC compiler capability or upstream CMake/install qualification |
| `test_cpp_tinyxml2_upstream_tests_native_linux_windows_project` | Supplied native compiler builds the same upstream test runner through the owned project route and its own tests succeed twice | Other configurations/platforms, full C++17, or a bootstrap fixed point |

```bash
uv run pytest -x -vv --tb=short -m 'not integration' tests/cpp
uv run pytest -x -vv --tb=short -m integration tests/cpp/test_cpp_owned_execution.py
PCC_CPP_ORACLE=/path/to/g++ uv run pytest -x -vv --tb=short -m integration \
  tests/cpp/test_cpp_projects.py::test_cpp_tinyxml2_explicit_cpp17_reference_oracle
PCC_PLATFORM_TEST_COMPILER=/path/to/pcc1 PCC_RUNTIME_ARCHIVE=/path/to/verified-runtime.a \
  uv run pytest -x -vv --tb=short -m integration \
  tests/cpp/test_cpp_projects.py::test_cpp_tinyxml2_native_linux_windows_project_exact_outcome
PCC_CPP_ORACLE=/path/to/g++ uv run pytest -x -vv --tb=short -m integration \
  tests/cpp/test_cpp_projects.py::test_cpp_tinyxml2_upstream_tests_explicit_cpp17_reference_oracle
PCC_PLATFORM_TEST_COMPILER=/path/to/pcc1 PCC_RUNTIME_ARCHIVE=/path/to/verified-runtime.a \
  uv run pytest -x -vv --tb=short -m integration \
  tests/cpp/test_cpp_projects.py::test_cpp_tinyxml2_upstream_tests_native_linux_windows_project
```

`PCC_PLATFORM_TEST_COMPILER` reuses the existing Linux/Windows platform C integration fixture;
there is no second C++ compiler-selection convention. This native adapter admits
ELF/PE artifacts only; it does not qualify Darwin/Mach-O or four-platform C++
support. The host execution adapter is further limited to Linux x86-64.
Other platforms need their own existing owner and real execution gates. The native test neither
provisions a compiler/runtime nor falls back to host Python or an oracle. It
requires a verified runtime, rejects interpreter wrappers/dynamic dependencies,
uses the existing process-tree watchdog, empties PATH, disables configured host
tools and keeps compiler/runtime/artifact identities in a receipt. Header checks
and sampled process observations have their stated scope; a static binary alone
is not proof of the source ownership of every linked byte.

**Both native project gates are currently unsatisfied.** Explicit-file host C++
dispatch is not native whole-project support. At source revision `ee43d357`,
`pcc/driver/project.py::_project_source_files` discovers only `.c` files;
`pcc/frontends/cpp/driver.py::preprocess_cpp` intentionally does not substitute
C compatibility headers for real C++ headers, and class/lifetime support is
incomplete. TinyXML2 needs those capabilities before its own runner can execute.
Fixture acquisition and ordinary harness tests do not resolve these blockers.
The gates deliberately fail when a
compiler/runtime/capability is missing; it has no skip, xfail, or success bucket
for unsupported constructs. Selecting only host tests must be reported as a
partial run, with the native gate explicitly unrun, not "integration green."

Keep stdout/stderr as bytes and execute the result twice. Smoke programs compare
all three outcome fields exactly; upstream tests retain all output and apply
their explicit result predicate rather than comparing performance timing bytes.
Negative tests assert diagnostic code and source location, and do
not publish an executable. Ordinary deterministic builds compare IR, every
separate-TU object, and the final image under identical options. Separate TUs
are linked as separate objects, never concatenated into a different program.

## First upstream project

`projects/tinyxml2-11.0.0/` contains the unmodified library, complete upstream
`xmltest.cpp` and `resources/`, original CMake/Make recipes and CMake helpers,
package-consumer test recipe, readme, and full license. `pcc-fixture.json` pins
the source-archive URL/SHA256 and each retained upstream file's SHA256, and
selects separate `smoke` and `upstream-tests` fixtures. The smoke driver's hash
is separate from the upstream inventory. No network acquisition is performed
by pytest. The fixture records
[TinyXML2 11.0.0, commit 9148bdf719e997d1f474be6bcc7943881046dba1](https://github.com/leethomason/tinyxml2/commit/9148bdf719e997d1f474be6bcc7943881046dba1).
The [upstream CMake recipe](https://github.com/leethomason/tinyxml2/blob/9148bdf719e997d1f474be6bcc7943881046dba1/CMakeLists.txt)
builds one library translation unit. Its
[documentation](https://github.com/leethomason/tinyxml2/blob/9148bdf719e997d1f474be6bcc7943881046dba1/readme.md)
describes its avoidance of C++ standard-library collection dependencies. It
still requires C/C++ headers, allocation, classes, namespaces, overloads,
templates and object lifetime; "small dependency footprint" does not mean
"already supported."

The independently authored smoke driver still exercises XML parsing, entity
decoding, integer attribute lookup, malformed-input rejection,
serialization/reparse, and stack-object destruction. It is **not** substituted
for the upstream test executable. `upstream-tests` compiles `tinyxml2.cpp` and
`xmltest.cpp` as distinct translation units without a PCC-authored main or
source edits. It preserves upstream `_FILE_OFFSET_BITS=64`. This first adapter
invokes the compiler directly; shipping the upstream CMake/Make files is not a
claim that their build/install workflows have run successfully.

The upstream executable runs with **no arguments**, from a fresh writable
source/resource copy, matching upstream CTest's argv and working directory.
An argument would select xmltest's single-file mode instead of its test suite.
Each of two runs gets a new `resources/out/`; generated outputs never touch the
vendor tree. Acceptance requires exit 0 and exactly one positive `Pass N, Fail 0`
summary. Counts must agree between runs. Stderr is retained without imposing
an empty-stream condition absent from the upstream CTest recipe. The timing
line remains in the raw stdout artifact but is not a deterministic byte gate.
The smoke fixture retains exact stdout/stderr/exit comparisons.

The preserved upstream license is Zlib. `PCC-NOTICE.md` distinguishes PCC
metadata and the smoke driver from upstream inputs, and identifies the separate
public-domain/copy-distribution notice retained inside `resources/dream.xml`.
CRLF, BOMs and binary regression resources are copied as bytes; local Git
attributes disable newline conversion, including the separately pinned smoke
driver. Vendor and materialized inputs and the manifest are rechecked before
and after use. New generated files are permitted; pinned inputs (including
`resources/out/readme.txt`) must remain unchanged. Reference/native compile
receipts and each run's `execution.json`,
`stdout`, and `stderr` remain under that run's output directory, not in this
repository. A reference pass is reference-only; compilation alone is
`compiled-not-executed`; unsupported native compilation is not a passed test.

## Coverage to grow, not to silently waive

For each newly implemented area, add positive ordinary tests, negative semantic
diagnostics, emitted execution, native pcc1 execution, and a project use site:

- preprocessing, literal encodings, fundamental types, cv/pointer/reference
  declarators, scope/qualified lookup and access control;
- expression value categories, overload ranking, conversions, sequencing,
  constexpr, templates/two-phase lookup/deduction/instantiation;
- class layout, constructors, copy/move, temporaries/destructors, inheritance,
  virtual dispatch, RTTI, exception matching/unwind/noexcept;
- lambdas, structured bindings, control flow, storage duration, TLS, ABI and
  separate-TU/ODR/linkage behavior;
- complete C++17 standard-library/runtime behavior, not just one project's
  header slice, tested independently through real libc++ and a dependency-ordered
  unchanged-project ladder. NumPy remains one downstream consumer, not the
  definition of the C++ goal.

Do not turn these requirements into expected failures that make a whole-language
completion claim green. Maintain exact receipts for executed scopes and leave
unimplemented, failed, and unrun boundaries visible.

## Arithmetic-overload execution increment

`tests/cpp/test_cpp_overload_execution.py` adds executed selection controls for
short-to-int promotion, float-to-double promotion versus integer conversion,
distinct same-width long/long-long overloads, bool identity, and the LP64 usual
arithmetic conversion of unsigned int plus long. Every positive case executes
the owned ELF twice and compares exact stdout/stderr/status with an explicit
C++17 reference. The host helper binds both C++ semantics and C lowering to the
same Linux x86-64 target, rather than borrowing the host's data model.

The original translation unit containing a long-double overload is retained as
a negative capability test. It requires an explicit
`cpp.unsupported.extended-float` diagnostic before object emission on the
selected target. **Extended-float value/ABI lowering remains unimplemented**;
this rejection is not a successful C++17 program execution. The separate
float/double/long positive case establishes only supported overload ranking.

## Single-translation-unit function templates

`tests/cpp/test_cpp_template_execution.py` executes arithmetic function-template
instances through the same owned emitter/linker and explicit C++17 oracle. The
cases distinguish int/double instances, ordinary-function preference only for
equal conversion sequences, definition-point lookup, instantiated promotion,
two type parameters with a dependent cast, and local alias/shadowing behavior.
Repeated builds compare IR/object/image bytes; each emitted program runs twice.

```bash
uv run pytest -x -vv --tb=short tests/cpp/test_cpp_template_execution.py
uv run pytest -x -vv --tb=short -m integration tests/cpp/test_cpp_template_execution.py
```

Negative controls require diagnostics before object emission for conflicting
deduction, an invalid selected template body, and a missing selected definition.
An invalid body cannot fall back to a worse ordinary overload or the external
oracle. The old blanket function-template rejection is replaced by the still
unsupported class-template boundary, not silently removed.

This is a single-TU arithmetic slice, not complete template support. Partial
ordering, ADL, class templates, and multi-TU weak ODR/COMDAT remain unqualified;
the harness does not internalize instances or concatenate TUs to conceal those
gaps. A two-TU `id<int>` program remains a required duplicate-strong-symbol
rejection in the owned linker and a separately named **reference-only** positive
control. Its successful external execution is not owned multi-TU template
support. Native pcc1 C++ project execution remains a separate unsatisfied gate.

## Scalar void/return effects

`test_cpp_void_execution.py` observes effects, not just acceptance of a void
body. Explicit/dependent discards must retain assignments; void-return calls,
template forwarding, empty returns and fallthrough must preserve call count and
the last observed argument. The owned assembler supplies generic scalar ABI
observer functions from `abi_observer.py`; a separate C++ TU supplies the same
observer contract to the external reference compiler. Neither observer
implements C++ source semantics or changes the translation unit under test.

`compile_host_cpp(..., owned_support_assembly=...)` records each owned support
object in its artifact. The default path is unchanged. Missing support and
duplicate source/support definitions fail at the owned linker; support cannot
silently replace a source definition. Invalid void values/returns must diagnose
before emission, without creating an executable or falling back to an oracle.

These checks execute Linux x86-64 scalar programs only. They do not establish
class temporary destruction, full discarded-value semantics for every type, or
native pcc1 frontend execution.
