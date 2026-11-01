# Java source frontend

The mainline is original Java source → owned parser → source semantic analysis
and typed AST → public PCC IR → self backend. Neither javac nor a JVM/class-file
intermediate is on this path. `compile_class` remains an explicitly separate,
bounded compatibility experiment, not the production representation.

```python
from pcc.frontends.java import compile_source, compile_sources

one = compile_source('class C { static int run() { return 42; } }')
project = compile_sources({
    'Main.java': 'package p; class Main { static int run(){return Helper.answer();} }',
    'Helper.java': 'package p; class Helper { static int answer(){return 42;} }',
}, entry_class='p.Main')
object_bytes = project.emit_object()
```

`compile_sources` accepts original source texts, not paths to open or classpath
entries. Every supplied class and method is checked and emitted. Class-qualified
method identities prevent cross-class symbol collisions, and all method symbols
are declared before bodies so recursive dependency graphs need no special case.
Input ordering does not affect IR. Existing `admitted_methods` describes the
selected entry class; `emitted_class_methods` additionally records `(class, name,
descriptor)` for the entire source closure.

The current scalar domain is one ordinary class per source unit, static boolean/integral/void
methods with boolean/integral parameters/locals, lexical scopes/definite assignment,
if/while/for/break/continue/return, recursive calls, int wrap/bitwise/masked shifts,
and integral/boolean casts. Primitive overload selection uses parameter types,
not return context or arity alone; numeric widening, int/long binary promotion,
compound-assignment narrowing and shift masks are explicit. Boolean does not
convert to or from an integer. Floating point, boxing and varargs remain unsupported.
The PCC native scalar ABI uses i1/i8/i16/i32/i64 lanes (boolean/byte/short or
char/int/long); this is not a JVM calling convention. Java byte/short/char
expressions and local slots instead use canonical i32 values, signed for byte
and short and zero-extended for char. Entry/call-result extension, argument/return
truncation and store/compound/conditional narrowing are explicit even when the
physical expression width does not change. On Linux x86-64, the complete Java
suite passed 960 ordinary and 320 integration cases (including 112 frozen
class-file compatibility cases, not source acceptance). All 38 complete narrow
originals matched the selected OpenJDK 21.0.12.1 observer's actual stdout, stderr
and exit status; 15 invalid originals were rejected. Independent assembly covers
B/S/C result widths and an eight-argument register/stack entry. The reference uses
explicit source/target 21 with the current JDK platform, not cross-release API
compatibility. Managed objects and arrays remain outside native execution.
Single-type imports and resolved static
class-qualified calls enforce class/member access and name obscuring. Unknown
roots of package-qualified calls fail explicitly until the shared bootstrap
public java.lang type directory can rule out implicit-type obscuring. Literal
fully qualified source fixtures are retained as capability rejections, not
rewritten to imports and counted as successful originals. Static imports, on-demand
imports, nested classes, generics and inheritance are not yet implemented.

Primitive `static final` constant-variable fields are checked and inlined, including
byte/short/char assignment-constant narrowing, field access, simple-name forward
reference restrictions and cross-class dependencies. Every initializer is checked:
`final` alone does not establish a constant variable. Nonconstant or cyclic static
initialization, mutable fields and blank finals require the shared initialization
contract and are rejected, even when unused. No Java storage or initialization
substitute is synthesized. Character literals represent one unsigned UTF-16 code
unit, with ordinary/octal Java escapes; Unicode-escape translation remains gated.

Initialized `final` boolean/integral locals and `final` parameters preserve their
lexical binding and prohibit reassignment. Only locals initialized by constant
expressions become constant variables; a runtime call initializer is still
evaluated exactly once and read from its local slot. Blank final locals remain an
explicit definite-unassignment boundary, not an assumed value or dropped declaration.

The primitive conditional operator `?:` is right-associative and uses standalone
boolean/integral typing, including byte/short/char constant narrowing before
ordinary numeric promotion. Its condition-sensitive definite-assignment states
are distinct from syntactic reachability. Only the selected arm executes in the
owned CFG; both arms are semantically checked, and a constant selected value does
not make a conditional expression constant when another operand is nonconstant.
Reference/poly/boxing conditional expressions remain explicit runtime boundaries.

Source-owned `switch` statements support byte/short/char/int selectors, checked
constant labels (including comma lists), colon fallthrough groups and arrow
statement/block rules. Duplicate/range checks, switch-block lexical scope,
definite assignment at every dispatch entry and break/continue targets precede
IR emission. Switch expressions, reference/enum/pattern labels and runtime
exception paths remain separate capabilities. `do` loops execute the body before
the boolean test, continue at that test, and intersect body/continue/break states;
constant conditions do not erase Java's syntactic reachability checks.

Labeled statements and labeled `break`/`continue` now bind typed target identities,
not label strings. Label scope is separate from local/method names; only a
directly labeled loop can receive `continue`. Matching exits and continues
participate in definite assignment, normal completion and loop update/test
selection. The complete Java domain passed 1016 ordinary and 354 integration
cases on Linux x86-64 (112 frozen compatibility cases excluded from source
acceptance). All 33 complete executable label originals matched the selected
OpenJDK 21.0.12.1 observer's actual stdout, stderr and exit status; 19 invalid
originals were rejected. Three intentionally nonterminating originals were
compiler-checked only, never executed. The reference uses source/target 21 on
the current JDK platform. Exception/finally unwinding and native managed objects
remain separate unsupported runtime domains.

Integral division/remainder and their compound assignments support nonzero
constant-expression divisors, preserving truncation toward zero, dividend-signed
remainders and int/long minimum divided by minus one. Neither float-based
constant evaluation nor overflowing signed IR division is used. Zero or dynamic
divisors remain unsupported until the shared ArithmeticException path exists;
an initialized non-final variable is not assumed constant.

`analyze_class_sources(mapping)` is a separate **semantic-model stage**, not a
native object capability: it reuses the source parser and scalar/control-flow
checker for primitive instance fields, ordinary receiver methods, explicit or
default constructors, same-class constructor delegation and `this` bindings.
Instance-initializer blocks are checked for receiver access, source-order field
references, local definite assignment and normal completion. Their typed local
frames and field initializers remain in one source-ordered construction plan;
only a constructor that invokes `super`, not one delegating with `this`, owns
that initialization sequence. This is plan metadata, not executed construction.
The simple-name forward-write exception is limited to `=`; compound assignment
and increment read the old value and are checked as forward reads.
Primary-qualified constant fields (`this.K`) retain receiver-access identity
and are not constant expressions for narrowing, case labels or reachability;
simple `K` and type-qualified `Record.K` constant names remain distinct.
It retains every body and initializer and rejects invalid receiver contexts,
constructor cycles and unassigned locals. Primitive payload slots have a
header-free layout plan; no object, tracing descriptor or vtable is registered.
Native `compile_source(s)` still rejects explicit instance features before IR
until shared managed allocation, roots, construction/publication and dispatch
are implemented. Final-instance-field DU, inherited Object overrides, references,
arrays and inheritance remain explicit model boundaries. This API is not a
separate runtime or evidence that a Java application can allocate objects.

Heap objects/arrays, runtime fields and class initialization, exceptions,
monitors, strings and library/runtime APIs remain explicit
capability errors. They must use the shared pcc-Python GC/scheduler/exception
contracts, not an independent Java runtime or a host-JDK fallback. A class's
implicit `Object` instance signatures cannot be redeclared as static methods.

Targets are admitted LP64 Linux x86-64/arm64 and Darwin arm64 owned backends.
Darwin object emission is not device-execution evidence. Tiny Linux executable
startup is an explicit `()I` export whose process status exposes only the low
8 bits; tests use a separate owned assembly observer for the complete int value.
This is not `main(String[])` startup, full Java conformance, a JDK implementation,
or an unmodified real-project qualification.

The next original-project target is the complete Apache Commons Numbers 1.2
`commons-numbers-primes` module and its original tests, pinned at commit
`77f715769e4039acb4babd9add80b78a06ca7279` (Apache-2.0). No source fragment from it
is claimed as project success. Its source closure requires arrays, boolean/long,
collections/generics, BigInteger, class initialization and exception support;
its original tests also require JUnit, annotations and lambda/stream support.
Original sources/notices are inventoried outside this repository; no upstream
implementation has been copied into the compiler.

### Source diagnostic contract

Every source capability boundary exposes an explicit stable
`java.unsupported.*` value on `UnsupportedJava.code`, mapped in
`specs/features/java.json`. The original diagnostic message, source location,
`str(error)` and `error.args` are preserved. Invalid source remains `JavaError`
with `code is None`; known malformed token/modifier/type starts are not counted
as unimplemented features. Encountering an unavailable grammar prefix does not
certify the rest of that input as valid Java. Resource limits and missing
supplied dependencies have separate codes. The frozen class-file compatibility
experiment retains its message-only errors and is not source acceptance.

These codes do not change the object/runtime boundary: class analysis is a
semantic model, final instance fields remain gated, and the unpublished blank
final model/reference disagreement is not part of this source revision.

### Primitive-array semantic model (not native arrays)

`analyze_class_sources` now checks one-dimensional boolean/byte/short/char/int/long
array parameters, results and locals, allocation/initializer expressions, length,
component reads and statement stores, primitive component conversions, reference
identity/null, and overload identity. Typed nodes retain the distinct JLS 15.26
simple/compound assignment evaluation and check order. Negative size, null access
and out-of-bounds access remain valid source with **unimplemented runtime effects**;
the analyzer does not replace them with constants or stack arrays.

`compile_source` / `compile_sources` still reject managed arrays before IR.
Array fields/layout, multidimensional/reference/float/double arrays, array casts
and inherited array methods remain outside this model. The authored complete
sources have a separate opt-in JDK compiler/execution reference lane; its outputs
are not PCC array-native execution or a raw native differential. The complete
Java test domain passed 906 ordinary and 281 integration cases on Linux x86-64.
The selected OpenJDK 21.0.12.1 reference ran 21 original programs, typechecked four
runtime-abrupt/instance examples without executing them, and rejected 22 invalid
originals. Its explicit source/target 21 contract uses the current runtime's
platform library, not cross-release API compatibility. Managed-array native
allocation and execution remain unimplemented.
