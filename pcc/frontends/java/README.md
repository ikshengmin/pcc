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

The current scalar domain is one ordinary class per source unit, static boolean/int/long/void
methods with boolean/int/long parameters/locals, lexical scopes/definite assignment,
if/while/for/break/continue/return, recursive calls, int wrap/bitwise/masked shifts,
and integral/boolean casts. Primitive overload selection uses parameter types,
not return context or arity alone; numeric widening, int/long binary promotion,
compound-assignment narrowing and shift masks are explicit. Boolean does not
convert to or from an integer. byte/short/char method/local declarations, floating point,
boxing and varargs remain unsupported. The scalar ABI emits boolean/i32/i64
IR lanes, and independent assembly tests observe their exact 1/4/8-byte returns. Single-type imports and resolved static
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

Heap objects/arrays, runtime fields and class initialization, exceptions including int
division/remainder, monitors, strings and library/runtime APIs remain explicit
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
