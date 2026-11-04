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

The current scalar domain is one ordinary top-level class and its static member
classes per source unit, static boolean/integral/void
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
rewritten to imports and counted as successful originals. Single-static imports
now bind accessible static fields and overloaded methods from supplied named-package
classes. Imports are validated even when unused, repeated declarations retain one
identity, and distinct owners stay distinct overload candidates. Own members and
local values retain their separate Java shadowing rules; ambiguous imported fields
are rejected at use. Imported fields also obscure expression-name qualifiers.
On-demand imports, static-imported member types, inner classes, generics and
inheritance remain unavailable.
The new single-static-import ordinary, native-byte and exact-original JDK obligations
are execution-UNRUN until CI validates this source. This lookup capability does not
supply JUnit, String objects, class initialization or any managed-runtime acceptance.

Native static member classes retain every original declaration, method and constant
field in one source closure. Canonical source names such as `p.Outer.Inner` are
distinct from binary owners such as `p/Outer$Inner`; ordinary dollar signs in
identifiers are not rewritten. All member methods, including uncalled ones, are
checked and lowered through the existing native primitive function/call/ABI path.
No constructor or enclosing receiver is synthesized, and no member body is skipped.

Lexical lookup searches the nearest enclosing class for types and constants. An
unqualified method name selects the nearest owner declaring that name, without
mixing outer overloads or bypassing inherited Object method names. Qualified calls
search only their explicit owner. Each intermediate qualifier retains Java's
field/method obscuring rules. Private access crosses classes of the same top-level
nest; every enclosing type must remain accessible. Imports are checked outside
that nest privilege and cannot import from an unnamed package. Canonical single-type
imports and single-static method/field imports may name nested owners; a static
import of a member type remains an explicit boundary, even when a same-named field
or method would otherwise be importable.

The new ordinary, exact native-byte and unchanged-source selected-JDK obligations
are execution-UNRUN. Complete pinned Commons Numbers `RegularizedGamma.java`, with
LICENSE, NOTICE and provenance, is retained as an original boundary input. Its real
static `P`/`Q` classes motivate this mechanism. With the private empty-constructor
declaration admission below, the unchanged native input is expected to stop at
`new Policy` on line 77, column 44. The separate class model still
rejects nested types at line 46. These are authored, unrun source predictions,
not complete-class or project acceptance. No extracted method or fake BoostGamma/
Policy dependency substitutes for that input. The original Primes String31 frontier
and shared managed-runtime/QPS gate remain unchanged. Nonstatic inner classes,
constructor execution, instance state, model nesting and runtime initialization
retain their separate capability boundaries.

Rules: [member classes](https://docs.oracle.com/javase/specs/jls/se21/html/jls-8.html#jls-8.5),
[name/access resolution](https://docs.oracle.com/javase/specs/jls/se21/html/jls-6.html),
[imports](https://docs.oracle.com/javase/specs/jls/se21/html/jls-7.html#jls-7.5),
and [binary names](https://docs.oracle.com/javase/specs/jls/se21/html/jls-13.html#jls-13.1).

Native static utility source may retain explicit `private C() {}` declarations
without acquiring managed constructor execution. These declarations must be
private, no-argument and syntactically empty: even an empty statement, a return
or an empty nested block is outside this initial domain. The original constructor
AST, owner, modifiers and tokens remain separate from ordinary methods, preserving
default-constructor suppression and allowing a distinct ordinary method named `C`.
Duplicate signatures and illegal constructor modifiers are rejected. Native sema
checks every constructor, and lowering rechecks its current AST before creating
any functions, so a later mutation cannot silently discard an executable body.

All ordinary static methods still enter primitive native lowering, including
uncalled methods. The constructors do not enter overload tables, static imports,
native exports or emitted method lists. Private access is not a reachability
proof: same-nest `new C()` is still rejected, including in uncalled methods or
`if (false)` branches. Explicit `this`/`super` invocation, instance state and
nonconstant class initialization retain their existing boundaries. An empty
constructor would still invoke `Object` construction if called; no such target,
receiver ABI, allocation, initialization or publication is implemented here.
The richer separate class model keeps its existing constructor-body checks.

Complete unchanged source maps exercise this static native path and independent
selected-JDK references. Their ordinary, native-byte, reference and whole-language
execution remains UNRUN. The original RegularizedGamma constructors at lines
36 and 48 have the admitted declaration shape; Q's constructor at 106 has the
same shape but parsing first stops at unsupported `new Policy` at 77:44.
Its real BoostGamma dependencies remain unresolved.
No complete original class/module/project acceptance follows from this admission.

Rules: [constructors](https://docs.oracle.com/javase/specs/jls/se21/html/jls-8.html#jls-8.8),
[preventing instantiation](https://docs.oracle.com/javase/specs/jls/se21/html/jls-8.html#jls-8.8.10),
and [class initialization](https://docs.oracle.com/javase/specs/jls/se21/html/jls-12.html#jls-12.4).

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
evaluated exactly once and read from its local slot.

Native primitive blank-final locals now have a separate JLS 16 definite-unassignment
pass after source binding and type/DA checks. A blank final is distinct from an
initialized final or final parameter. Every simple assignment must still find it
definitely unassigned after its right-hand side; compound assignments and increments
remain errors. DU is not the complement of DA. The typed-source flow graph intersects
branches and switch entries, preserves boolean outcome rules and abrupt targets,
and computes a greatest fixed point through loop/continue backedges. Fresh lexical
scope entries reset their own bindings, including switch dispatch that bypasses a
declarator; case-label fallthrough and outer declarations do not reset them. Syntactically
analyzed but runtime-unreachable for updates and do conditions retain their vacuous
flow rules. No source expression is rebound, retyped or emitted by this pass.

An uninitialized declaration emits no store; a later legal assignment uses the
existing native primitive slot/store path. A later-assigned final never becomes a
constant variable for narrowing, case labels or reachability. Class-model blank
locals remain explicitly gated in methods, constructors, initializers and enhanced-for
contexts until all those frames have a complete DU owner. Blank final fields and
managed reference storage retain their separate boundaries.

The new ordinary, owned-native and unchanged-source JDK obligations are execution-UNRUN.
This mechanism occurs in the complete pinned Commons Numbers
[Precision.java](https://github.com/apache/commons-numbers/blob/77f715769e4039acb4babd9add80b78a06ca7279/commons-numbers-core/src/main/java/org/apache/commons/numbers/core/Precision.java#L224-L243)
and
[Complex.java](https://github.com/apache/commons-numbers/blob/77f715769e4039acb4babd9add80b78a06ca7279/commons-numbers-complex/src/main/java/org/apache/commons/numbers/complex/Complex.java#L3572-L3593).
That is source relevance, not native compilation of either complete class or project.
The original Primes String31 frontier and shared managed-runtime/QPS gate are unchanged.

Primitive local type inference now follows JLS 14.4/14.4.1 on the ordinary owned
source-to-native route. A single `var` declarator in a block or basic-for initializer
is checked without an assignment target type. Its standalone boolean, byte, short,
char, int, long, float or double result becomes an ordinary typed local binding;
the existing native slot, conversion, store, load and ABI owners are unchanged.
The initializer is checked and emitted once. `final var` retains constant-variable
rules, while a non-final initializer does not become a constant variable. The name
is in scope during inference, so self-dependencies fail even in a dead branch;
an unqualified same-named method still belongs to the separate method namespace.
The original `var` token and inferred concrete type are both retained. Ordinary
variables, methods and package segments may be named `var`; declared type names
may not. Missing/multiple initializers, appended array brackets, null and void
inference remain ordinary source errors.

These new primitive-inference ordinary, native-byte and unchanged-original JDK
obligations are **execution-UNRUN**, not observed native acceptance. Reference,
array and enhanced-for inference remain explicit separate capabilities. Native
managed objects, strings, arrays and exceptions remain unavailable. Apache Commons
Numbers targets Java 8 and does not use `var`, so this independent Java 10+ language
capability does not move its original-project frontier or change the runtime/QPS gate.

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
constant evaluation nor overflowing signed IR division is used. Native zero or
dynamic divisors remain unsupported until the shared ArithmeticException path
exists; an initialized non-final variable is not assumed constant. The separate
class model retains checked dynamic/zero source plans, described below.

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
Native `compile_source(s)` still rejects instance execution features before IR;
retaining private empty constructor declarations does not implement managed
allocation, roots, construction/publication or dispatch.
Blank-final-instance-field DU, inherited Object overrides,
general references and inheritance remain explicit model boundaries. This API is not a
separate runtime or evidence that a Java application can allocate objects.

The class-model stage now retains named field, parameter, result and local type
tokens and resolves simple type names to supplied source classes using the same
own-class, single-type-import, same-package and supplied-public-java.lang lookup
order. Canonical class identities and source descriptors support identity/null
assignment, same-type/null equality, method/constructor arguments and returns,
without a guessed reference representation. Reference fields have null default
metadata and unknown offset, size and alignment; a mixed primitive/reference
payload has no aggregate layout. These new obligations are execution-unrun.
Native declarations and lowering remain closed. Missing ordinary class sources,
qualified/member type metadata, generics, reference arrays, inherited conversions,
reference casts and managed receiver dereferences remain explicit boundaries.

In the unchanged Commons Numbers original, the reported result-type diagnostic
at Primes.java:31 actually belongs to its static final String field. The model
parser can now retain that declaration's original type tokens and UTF-16 literal
AST, but this does not supply java.lang.String, a constant String object, literal
interning or class initialization. The shared managed envelope is opt-in GC0
infrastructure, not a String layout or language runtime. No native or complete
Commons project acceptance follows, and its strict whole-project gate is unchanged.

The partial class model additionally records the expected bootstrap String type
as a distinct unresolved dependency, not as a supplied class. String literal and
constant-name/group/String+String content may carry immutable UTF-16 source facts,
including static-final and final-local constant variables. These facts are not
numeric constants or runtime reference identities. Per-value and aggregate folding
budgets are checked before concatenation. Normal String name shadowing still
applies to declarations; a literal always has bootstrap String type.

Models expose compiler-context obligations for the missing owned bootstrap
definition, managed literal materialization/interning and shared owning-root ABI.
They remain incomplete and unregistered. Project and class models keep
semantic_context_complete false, while string_context_complete describes only
whether String-specific obligations are absent. A constant static-field read
does not imply initializing its declaring class. String equality is explicitly gated
until constant-expression/identity behavior can preserve flow diagnostics;
String methods/format, switches, mixed or runtime concatenation and casts remain
unsupported. Native source admission remains closed, and the scalar lowerer
rejects unknown/reference types rather than guessing an integer representation.
This is another source-model prerequisite, with all execution still UNRUN.

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
semantic model, blank final instance fields remain gated, and the unpublished blank
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
Managed array-field layout, multidimensional/reference/float/double arrays, array casts
and inherited array methods remain outside this model. The authored complete
sources have a separate opt-in JDK compiler/execution reference lane; its outputs
are not PCC array-native execution or a raw native differential. The complete
Java test domain passed 906 ordinary and 281 integration cases on Linux x86-64.
The selected OpenJDK 21.0.12.1 reference ran 21 original programs, typechecked four
runtime-abrupt/instance examples without executing them, and rejected 22 invalid
originals. Its explicit source/target 21 contract uses the current runtime's
platform library, not cross-release API compatibility. Managed-array native
allocation and execution remain unimplemented.

### Primitive-array instance fields (source-only prerequisite)

The same parser and class analyzer now retain one-dimensional primitive-array
instance fields, including per-declarator brackets, null defaults, field/array
initializers, ordered instance initialization, receiver and unqualified reads,
length, reference rebinding and component stores. Local/parameter shadowing and
simple-name forward-reference restrictions apply to the field reference itself.
Declaration-initialized `final` array fields cannot be rebound, but their
components can change; these references and their lengths are not constants.
Blank finals, static array initialization and general object references stay
explicit unsupported boundaries. JLS 4.12.4/5, 8.3.1.2, 8.3.2/3 and 10 govern
this semantic slice.

The class model retains array fields in declaration order with a null default;
reference slot size/alignment and **all field offsets and aggregate layout** in
a mixed reference class remain unknown (`None`). It does not guess pointer
width, allocate objects, register GC descriptors or lower references into scalar
IR. Existing primitive-only layout plans are unchanged. Native `compile_source`
and `compile_sources` still fail closed before managed storage can reach IR.

`tests/java/test_source_array_fields.py` contains complete independently authored
originals for semantic checks, invalid Java, native rejection and an opt-in JDK
reference. Only the separate JDK observer allocates instances and runs those
originals; it is not a PCC native comparison. All new execution, including
ordinary, reference and whole-Java validation, is **UNRUN**, delegated to CI.

This prerequisite was selected by source-reading ZXing 3.5.3, release commit
`f0f1a94439db0300ff0c4618f4e0a41aa630aeac`:
[GenericGF](https://github.com/zxing/zxing/blob/f0f1a94439db0300ff0c4618f4e0a41aa630aeac/core/src/main/java/com/google/zxing/common/reedsolomon/GenericGF.java),
[GenericGFPoly](https://github.com/zxing/zxing/blob/f0f1a94439db0300ff0c4618f4e0a41aa630aeac/core/src/main/java/com/google/zxing/common/reedsolomon/GenericGFPoly.java), and
[ReedSolomonEncoder](https://github.com/zxing/zxing/blob/f0f1a94439db0300ff0c4618f4e0a41aa630aeac/core/src/main/java/com/google/zxing/common/reedsolomon/ReedSolomonEncoder.java).
The inspected files carry Apache-2.0 notices; no upstream implementation was
copied. Their blank-final fields still need definite-unassignment analysis;
objects/arrays need managed execution, and `System.arraycopy`, exceptions,
native dynamic remainder, native array iteration, strings and collections remain further gaps.
This change does not compile or qualify ZXing, nor establish Java-as-Python
extension support. The bytecode/JAR compatibility experiment is unchanged.


## Supplied-source class creation bindings

The nonlowering class API now checks simple-type `new Target(args)` expressions
against the actual supplied class and constructor declarations. Type-name lookup
is independent of local value names. Public/package/private/protected access,
default constructor identity, strict fixed-arity widening/null applicability and
unique overload selection remain source rules. Calls bind the same constructor
object that owns the checked initialization/delegation metadata, including when
an earlier class refers to an empty-method class's implicit constructor.
Original argument nodes are retained in order, and a selected String formal
adds the caller's unresolved String ABI obligations even for a null argument.

Every creation site instead of creating an object records an immutable missing
runtime contract: class initialization, allocation/default initialization and
allocation failure before argument evaluation, left-to-right arguments and
abrupt completion, selected constructor/super construction, fresh identity,
managed layout, publication and owning-root/safepoint integration. No reference
constant, object address, managed layout, runtime registration or native lowering
is supplied. Argument source typing is not argument execution. Primitive payload
metadata still does not define a managed object header or allocation ABI.

Qualified, generic/diamond, anonymous and created-reference member operations
remain explicit wider domains. Bootstrap String construction and other missing
classes require actual owned definitions. Boxing/unboxing, inherited conversion,
varargs, exception handling and native class allocation are not implemented by
this strict-invocation subset. Static nonconstant reference initialization is
still rejected. All new source/native-boundary and separate same-original JDK
obligations in `test_source_construction_model.py` are UNRUN; JDK object execution
is never counted as PCC object execution.

Commons Numbers' unchanged Primes source uses class creation inside `throw` at
line 70. The nonlowering parser retains its original `final int p` enhanced-for
at line 53 and the throw/creation/format-call AST at line 70. The next expected
parser boundary is the generic `List<Integer>` return type at 108:23; the exact
complete-original regression is authored but execution-unrun. Retaining that
syntax does not typecheck or implement IllegalArgumentException or String.format.
Production scalar compilation still rejects the static String field at line 31.
Static arrays, native dynamic remainder, throw/format, List/Integer and other generic,
library, bootstrap and runtime dependencies remain unresolved. The full original
production/test closure and strict project gate are unchanged.

Rules: [class creation](https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.9),
[constructor selection](https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.9.3),
and [creation order](https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.9.4).

### Primitive-array enhanced-for source model

`analyze_class_sources` retains the original enhanced-for declaration, array
expression and body without rewriting tokens or synthesizing Java locals. It
checks the expression once in the enclosing scope, then introduces the iteration
binding only for the body. Boolean/integral array components use assignment
conversion to the declared variable, including numeric widening to float/double;
an array read is never a constant expression eligible for narrowing. A final
iteration binding is initialized each iteration and cannot be reassigned.

The immutable `ArrayIterationPlan` reuses the expression's shared compiler-only
managed value plan. It records a captured reference independent of later source
variable rebinding, an int index, null/length/read order, per-iteration conversion,
and increment/retest on normal body completion or continue targeting that loop.
Break, return and outer-loop continue leave it. The captured reference needs a
relocation-aware root across body calls/backedges and root retirement on every
exit; these obligations remain unmet, not a root slot or allocation ABI. Array
elements remain live reads, not a snapshot copied at loop entry.

Scope, label targets, reachability and definite assignment use the existing
source owners. An empty-array exit preserves expression assignments but does
not establish assignments made only in the body, even when an initializer is
syntactically nonempty or the body always returns. Blank final locals in this
class-model path remain gated. Iterable iteration, inferred `var`, boxing, reference/floating/
multidimensional arrays and native managed iteration remain unsupported.

`test_source_enhanced_for_model.py` contains exact complete-source model and
native-rejection obligations plus a separate selected-JDK reference observer.
Only the JDK lane executes managed loops; no PCC native array execution is
claimed. All new execution and complete-Java qualification are UNRUN, CI-only.

Rules: [enhanced for](https://docs.oracle.com/javase/specs/jls/se21/html/jls-14.html#jls-14.14.2),
[scope](https://docs.oracle.com/javase/specs/jls/se21/html/jls-6.html#jls-6.3),
[assignment conversion](https://docs.oracle.com/javase/specs/jls/se21/html/jls-5.html#jls-5.2),
and [definite assignment](https://docs.oracle.com/javase/specs/jls/se21/html/jls-16.html#jls-16.2.12).

### Null-typed throw source semantics

The nonlowering class parser retains ordinary throw statements and throw rules
in arrow switches. A dedicated source owner checks the original operand exactly
once and then classifies its static type. Only null-typed operands currently
have a successful checked model. Grouping and null/null conditional expressions
remain null-typed; a class, String or array variable initialized to null does not.
Primitive, void and array operands are ordinary type errors. Class and bootstrap
String operands require the missing owned Throwable hierarchy and checked-
exception analysis and fail with `java.unsupported.throwable-types`. Exception-
looking class names never supply that proof. Method/constructor `throws` clauses
also have that explicit capability boundary; their declaration/effect domain is
not implemented by this change.

A successful throw model has no normal successor. Existing reachability and
definite-assignment joins therefore exclude its branch, without treating it as
break or continue. A constructor may complete abruptly, but an instance
initializer still must be able to complete normally. The source owner retains
every method and statement, including uncalled methods and conditionally
unselected throw branches.

`ThrowSourcePlan` separates operand failure from successful null evaluation. An
exception raised while evaluating the operand propagates unchanged; only normal
null evaluation requires creation of NullPointerException. Its canonical name
is a language-defined source fact, not a supplied class or registered type.
Allocation/initialization failures, owning roots and safepoints, handler matching,
cleanup, unwinding, cross-language policy and uncaught delivery remain unmet
contracts. Throw sites join the class/project compiler-context obligations;
`throw_context_complete` describes only absence of explicit sites, never runtime
availability. No exception object, root slot, handler table or native code exists.

The normal scalar entrypoints still reject throw statements before IR. The
complete originals in `test_source_throw_model.py` have model/type/flow and native
rejection obligations plus separate JDK catch-and-observe wrappers. Only those
JDK observers execute exceptions; originals are not rewritten. All new execution,
including the original Primes parser expectation, remains UNRUN and CI-only.
The production String-field frontier and complete original-project gate remain.

Rules: [throw](https://docs.oracle.com/javase/specs/jls/se21/html/jls-14.html#jls-14.18),
[exception checking](https://docs.oracle.com/javase/specs/jls/se21/html/jls-11.html#jls-11.2),
and [abrupt definite assignment](https://docs.oracle.com/javase/specs/jls/se21/html/jls-16.html#jls-16.2.13).

### Dynamic integral arithmetic source semantics

The nonlowering class model checks dynamic and zero integral `/`, `%`, `/=` and
`%=` without requiring a fabricated constant divisor. Existing operand owners
still check the complete original expressions exactly once, perform int/long
numeric promotion and enforce variable, finality and definite-assignment rules.
The separate `IntegralArithmeticPlan` preserves operation and compound-target
facts without replacing original operators or children. Proven nonzero constant
divisors keep their existing source/native owner. Floating division is unchanged;
floating remainder retains its explicit capability boundary.

The arithmetic check follows normal completion of both operands. Failure in an
operand propagates its original exception, rather than becoming divide-by-zero.
Compound assignment remembers both the variable and its old value before the
RHS. Array component assignment checks reference/index/null/bounds and saves
the old component before the RHS; receiver-field and local targets retain their
own saved-variable plans. A normal nonzero operation truncates its quotient
toward zero, preserves Java's MIN/-1 quotient and zero remainder, then performs
compound conversion and storage. Neither operand failure nor division failure
stores a result. No compound target is evaluated twice.

An integral zero-divisor expression is valid source, but is not a constant
expression. It cannot supply constant narrowing, a case label or constant-flow
proof. It also does not make following statements syntactically unreachable:
reachability and definite assignment remain with their existing source owners.
Source constant folding never invokes host integer division on zero.

Dynamic/zero sites carry immutable obligations for their actual method,
constructor, instance initializer or field initializer. They contribute to the
class's unmet managed requirements. `integral_arithmetic_context_complete` only
states that no such sites were found. ArithmeticException identity is a required
language fact, not an owned exception class or runtime registration. Allocation,
owning roots, safepoints, handler matching, cleanup, unwinding and uncaught
delivery remain unavailable. No native instruction, exception object or handler
table is produced by this model.

This addresses the source domain used by unchanged Primes' `n % p` at line 54
and SmallPrimes' dynamic division/remainder and long-promoted remainder. It does
not advance their observed project frontier: complete Primes model parsing still
expects the generic return-type gap at 108:23, production compilation still
stops at String31, and SmallPrimes model parsing still stops at static-array
storage39. These complete-original guards retain the original source and are
authored, execution-UNRUN expectations. The project inputs and whole-project
gate are unchanged.

`test_source_integral_arithmetic_model.py` has complete original source cases
and separate selected-JDK observers for values, exception identity, operand
effects and failed stores. Only the JDK executes dynamic/throwing arithmetic;
the PCC scalar entrypoints still reject it before lowering. All new ordinary,
native-rejection, reference, whole-Java and original-project execution is UNRUN.

Rules: [operand order](https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.7),
[division](https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.17.2),
[remainder](https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.17.3),
[compound assignment](https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.26.2),
and [constant expressions](https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.29).
