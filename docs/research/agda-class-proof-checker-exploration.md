# An Agda-class proof checker in pcc: driving a Python edition of church-turing

Assessment: 2026-11-12. **Research-only exploration. No code, test, CLI,
workflow or priority change.** pcc compiled and executed nothing for this note;
the only execution is one host-CPython run of the syntax sketch below.

## Question

Stated purpose (translated from the maintainer's request of 2026-11-12): pcc
should be, for a Python edition of the church-turing development, what Lean is
for its Lean edition and Agda is for its Agda edition. That is: read the
edition's definitions, statements and proofs, check them, and run its
executable part.

This note explores what "a system like Agda" inside pcc would consist of, what
the existing editions demand from it, what pcc already has, and a staged route.
Everything under "Recommendation" is the author's judgment, not a decision.

## Assessment in brief

1. It fits the ownership contracts. A proof checker is an ordinary Python
   program using only the standard library. It runs under host CPython and is
   compiled by pcc into a native checker. Agda, Lean and GHC stay outside the
   product path, usable only as labeled reference oracles.
2. Recommendation: build an **Agda-class** system (explicit proof terms,
   dependent pattern matching, coverage and termination checking), with the
   Agda edition as its first corpus. That edition is 75 modules of explicit,
   constructive, `--safe` proofs with no tactics. The Lean edition is about
   thirty times larger and is written in Mathlib tactics; re-authoring it needs
   a tactic framework and a library pcc does not have.
3. A Lean-compatible replay kernel is a different tool with a different
   meaning. It would re-check Lean's exported terms; it would not produce a
   Python edition. Keep it as an optional later track.
4. The first blocker is not in the checker. pcc's owned Python parser lowers
   `match` class patterns without `__match_args__`, so the constructor-class
   idiom every such edition is written in probably does not behave as in
   CPython (source reading, unexecuted).
5. This is new work outside the current frontend priority in `AGENTS.md`.
   Selecting it is the maintainer's decision.

## The existing editions

Counts are keyword censuses over source text (comments included). They size the
surface; they are not elaborated inventories.

| Edition | Identity | Size | Driven by |
| --- | --- | --- | --- |
| Lean | `church-turing` at `5474f4f`, Lean/Mathlib/cslib v4.34.0 | `Start/`: 2,209 modules, 496,700 lines, 27,416 `theorem`/`lemma`, 12,572 `def`/`abbrev`, 1,100 `inductive`/`structure`/`class` | `lake build --wfail` plus the repository's packaging gate (manifest, `sorry`, closure checks) |
| C | `c/` in the same repository | Round 1: base, lambda, Cobham terms, QBF. Round 2: seven machine modules; parts G-K not started | `make test`; properties proved by an external prover |
| Agda | `ChurchTuring` at `7aaed61` (2026-10-04), Agda with standard-library 2.3 | 75 modules, 17,059 lines, about 1,245 top-level signatures | one `agda` invocation per module |

Lean edition, proof style: about 39,700 `by` blocks, 25,949 `simp`, 11,814
`omega`, 2,045 `decide`, 203 `grind`, 64 `aesop` occurrences, no
`native_decide`. `noncomputable` occurs 2,076 times. Its README states that the
headline results depend only on `propext`, `Classical.choice` and `Quot.sound`.

C edition, what it teaches: its contract is "whenever a function returns
`CT_OK`, its result equals the Lean value", with the Lean definitions restated
in `c/spec/CTSpec.lean`. Its task board records 64 round-1 properties reported
proved with no proof artifact returned, so that verification cannot be
re-checked, and 2 of 32 round-2 properties proved. An executable edition is
only as trustworthy as the artifacts someone else can re-check. That is the
argument for a checker the project owns.

Agda edition, what it demands: 74 of 75 modules carry `--safe`. Its recorded
scope is that every Lean result is independently verified in Agda; keeping the
Lean source or a successful Lean build does not count. Its ledger pins 470 Lean
modules with 7,996 named claims, of which 107 have reviewed Agda proofs.

Agda feature use (regex census):

| Used heavily | Count | Not used |
| --- | ---: | --- |
| `Fin` | 489 | `postulate` (the one hit is an identifier) |
| `with`-abstraction | 371 | termination or positivity override pragmas |
| absurd patterns `()` | 258 | instance arguments |
| `where` blocks | 253 | `--without-K`, cubical |
| `rewrite` | 232 | tactics, reflection |
| pattern synonyms | 211 | user-level universe polymorphism (3 hits) |
| `Dec`/`yes`/`no` | 204 | coinduction, sized types |
| `Σ`/`∃` | 199 | |
| pattern-matching lambdas | 132 | |
| `data` 37, `record` 26, `Vec` 37, parameterized modules 20, `Set₁` 17, `mutual` 1 | | |

Standard-library surface: `Data.Nat`, `Data.Fin`, `Data.Vec`, `Data.List`,
`Data.Maybe`, `Data.Product`, `Data.Sum`, `Data.Bool`, `Data.Empty`,
`Data.Unit`, `Relation.Binary.PropositionalEquality`, `Relation.Nullary`,
`Data.List.Relation.Unary.{All,Any}`, list membership, and the `Properties`
modules for naturals, lists and `Fin`.

This census is the requirement list. A checker that handles inductive
families, dependent pattern matching with absurd cases, `with` and `rewrite`,
records, `Set` and `Set₁`, and implicit-argument inference covers the corpus.
It does not need tactics, type classes, coinduction or universe polymorphism to
start.

## What "like Lean" and "like Agda" decompose into

| Role | Lean | Agda | pcc today |
| --- | --- | --- | --- |
| Surface language | Lean syntax, tactics, macros | Agda syntax, mixfix, pattern clauses | Python, through an owned parser |
| Elaboration | large elaborator, unifier, type classes | implicit arguments, `with`, case splitting | type inference for lowering; no logic |
| Trusted checking | small kernel over fully explicit terms | the type checker itself, including coverage and termination | none |
| Re-checkability | export format and independent kernels | no comparable exported core (from general knowledge, not re-verified) | not applicable |
| Running definitions | separate compiler; outside the kernel's trust | GHC and JS backends | native code through the self backend |
| Driver and gates | `lake`, `--wfail`, `sorry` and axiom audits | per-module check, `--safe` | compiler CLI only |

pcc already supplies the bottom half: a parser and a native execution path for
Python. The top half does not exist. Existing formal work in this repository is
all oracle-style and says so:

- `pcc/diagnostics/contracts/category.py` opens with "This is not a proof
  assistant"; it is an executable model of composition laws.
- `scripts/research/pcc_ttf_verified/FoldCompare.lean` proves one compare-fold
  rewrite in Lean; `VERIFIED_FOLD.md` records the transfer gap between that
  model and the Python source.
- `specs/tla/` and `scripts/research/flow_gc_tlc_check.py` model one GC
  transfer protocol in TLA+, with a deliberately broken negative configuration.

None of these checks a proof inside pcc. The pattern worth keeping from them is
the explicit transfer gap and the mandatory negative control.

## Contract consequences

- **Ownership.** `docs/compiler-contract.md` allows only CPython and its
  standard library for host pcc and no external toolchain for pcc1. Agda,
  Lean, GHC, `lean4export` and any SMT solver are reference tools at most.
  Local inventory at the time of writing: Agda 2.8.0, GHC 9.6.7, Lean through
  elan.
- **Mode labels.** "Checked under host CPython" and "checked by the
  pcc1-compiled checker" are different evidence, as are "proof accepted" and
  "compiled definition behaves as the proof assumes".
- **No special cases.** The checker and the Python edition are ordinary
  programs. If they need a compiler capability, that capability is fixed
  generically.
- **CLI parity.** A public check command would fall under the same
  `pcc` / `python -m pcc` / `pcc1` parity contract as every other command.
- **Priority.** `AGENTS.md` puts the C++, Rust, Java and Go frontends first.
  This work is none of them and must not displace them unless reselected.

## Design sketch

### One source, two readings

Python-edition modules are valid Python.

- **Execution reading.** Ordinary Python semantics. CPython runs the module;
  pcc compiles it. No erasure step exists because nothing was added.
- **Logical reading.** A restricted fragment is read as terms of a dependent
  type theory. Annotations are types, indexed classes are inductive families,
  and a function whose result type is a proposition is a proof.

The checker accepts only the fragment where the two readings agree by
construction: frozen data, no mutation, no identity, total structural
recursion, exhaustive `match`.

The following fragment parses and runs under host CPython 3.15.0, where `Data`
is a stand-in base class whose `__class_getitem__` returns the class. It was
not compiled by pcc, and no checker gives the annotations meaning yet.

```python
class Term(Data): pass

@dataclass(frozen=True)
class Var(Term):
    index: Nat

@dataclass(frozen=True)
class Lam(Term):
    body: Term

@dataclass(frozen=True)
class App(Term):
    fn: Term
    arg: Term

def lift(c: Nat, t: Term) -> Term:
    match t:
        case Var(i):
            return Var(i) if i < c else Var(i + 1)
        case Lam(b):
            return Lam(lift(c + 1, b))
        case App(f, a):
            return App(lift(c, f), lift(c, a))

# One-step beta reduction: an indexed family. Type parameters are the
# implicit arguments; the base subscript gives each constructor's indices.
class Step[t: Term, u: Term](Data): pass

@dataclass(frozen=True)
class Beta[t: Term, u: Term](Step[App(Lam(t), u), subst(u, 0, t)]): pass

@dataclass(frozen=True)
class AppL[t1: Term, t1p: Term, t2: Term](Step[App(t1, t2), App(t1p, t2)]):
    step: Step[t1, t1p]

class Steps[t: Term, u: Term](Data): pass

@dataclass(frozen=True)
class Done[t: Term](Steps[t, t]): pass

@dataclass(frozen=True)
class More[t1: Term, t2: Term, t3: Term](Steps[t1, t3]):
    head: Step[t1, t2]
    tail: Steps[t2, t3]

# A theorem is a function; its proof is a total program.
def steps_trans[t1: Term, t2: Term, t3: Term](
    r1: Steps[t1, t2], r2: Steps[t2, t3]
) -> Steps[t1, t3]:
    match r1:
        case Done():
            return r2
        case More(s, r):
            return More(s, steps_trans(r, r2))
```

Because proofs are programs here, a derivation is a value: running
`steps_trans` on concrete reductions returns the composed reduction. The Agda
edition already works this way (its compilers return traces), so a natively
compiled Python edition can emit witnesses that the checker re-checks.

### Mapping from the Agda corpus

| Agda | Python surface | State |
| --- | --- | --- |
| `data T : Set` | base class plus frozen constructor classes | sketched |
| indexed family | generic class; constructor base subscript carries indices | sketched |
| implicit arguments | type parameters on `def` and `class` | sketched |
| function clauses, absurd patterns | `match`; impossible cases omitted and discharged by coverage | sketched |
| `with e` | `match e:` on a non-variable scrutinee, with goal abstraction | needs design |
| `rewrite eq` | no natural Python form | open |
| pattern synonyms | `match` has no user-defined pattern abbreviations | open (211 uses) |
| `where`, pattern lambdas | nested `def` | straightforward |
| records | frozen dataclasses | straightforward |
| parameterized modules | no direct counterpart | open (20 uses) |
| mixfix operators | named functions | cosmetic |

### Components

1. **Reader.** Reuse the owned parser (`pcc/frontends/python/py_parse.py`) so
   the host and native checkers read the same tree. It already parses type
   parameters on `class` and `def`.
2. **Elaborator.** Implicit-argument inference, `with`-abstraction, coverage
   checking, translation of `match` to case trees.
3. **Core checker.** Dependent function types, `Set`/`Set₁`, inductive
   families with positivity, case trees, termination, conversion by
   normalization.
4. **Prelude.** The standard-library subset listed above, ported with its
   used lemmas. Licence and notices need per-file review under the source
   discipline in the multi-language roadmap.
5. **Driver and gates.** The counterpart of `--safe` and of the Lean
   edition's packaging gate: check every module, reject escape hatches, list
   assumptions, record source hashes.
6. **Ledger.** A proof map from Python symbols to Agda symbols and Lean
   claims, following the Agda edition's inventory.

### What is trusted

- The core checker, including coverage, termination and index unification.
  This is the Agda trust model, larger than a Lean-style kernel.
- The reader's interpretation of Python syntax as logic.
- The prelude's primitive correspondences, for example natural numbers as
  non-negative Python integers.
- For the execution reading only: pcc's compiler and runtime. A proof about
  `lift` is about the logical reading. That native `lift` computes the same
  function is a compiler-correctness assumption, the same one Lean and Agda
  make about their compilers. Differential execution is test evidence.

### Where Python and type theory disagree

- **Naturals.** Python `-` is not truncated subtraction and `int` includes
  negatives. The fragment needs a natural-number type with explicit monus.
- **Identity and mutation.** Excluded: `is`, `id`, mutable containers. This is
  the same boundary the opt-in value model draws for identity-free payloads.
- **Totality versus completion.** A function accepted as terminating can
  still hit the recursion limit on a deep input
  (`pcc/runtime/py/py_recursion_runtime.py` raises `RecursionError`). Claims
  about execution are partial correctness unless depth is bounded, as in the C
  edition's fuel and limit statuses.
- **Fall-through.** A `match` with no matching case returns `None` in Python.
  Coverage checking has to make that unreachable.
- **Equality.** Structural `==` on frozen dataclasses is a program; `≡` is a
  type. Decidable equality must be derived and proved.
- **Eager evaluation of indices.** CPython evaluates base-class subscripts
  when a class is created. In the sketch, `subst(u, 0, t)` runs on type
  variables at import time and happens to return `None`. This is the weakest
  point of the surface and needs a rule or a deferred form.
- **Classical reasoning.** The Agda corpus is constructive and axiom-free. The
  Lean capstones that use choice are out of reach without a labeled axiom mode.

### pcc gaps the sketch exposes

Source reading only; re-probe before relying on any of these.

- `_match_pattern_condition_bindings` in `py_parse.py` lowers a class pattern
  to `isinstance` and then binds one positional sub-pattern to the subject
  itself and several to `subject[0]`, `subject[1]`, .... `__match_args__` does
  not occur in `pcc/` or `tests/`. `case App(f, a)` on a dataclass therefore
  likely differs from CPython. This is a Python-semantics gap independent of
  any proof system.
- Guards, or-patterns and keyword sub-patterns have no dedicated handling in
  that function.
- Type parameters are parsed for `class` and `def`; their lowering was not
  examined.

## The Lean replay alternative

Lean can export its checked environment with `lean4export` as NDJSON, and an
independent kernel can re-check it. The [Lean Kernel Arena][arena] compares
such kernels on accept and reject tests. Search summaries on 2026-11-12 (not
read first-hand) report: one kernel written in RPython, [rpylean][rpylean], at
80 of 94 accepted and 25 of 42 rejected; an `Init` export of 309.5 MB and 6.1
million lines; a CSLib export of 1.2 GB and 22.8 million lines. The Mathlib
export size and check time were not confirmed.

What a pcc-built replay kernel would give: every Lean-edition theorem
re-checked by pcc-compiled native code, and a long-running, allocation-heavy
real workload for the five collectors. What it would not give: a Python
edition, or independence from Lean's elaborator and exporter. It also needs
Lean's type theory (recursors, proof-irrelevant propositions, quotients,
universe polymorphism), which the Agda corpus does not.

A second alternative, an Agda backend that emits Python, would need a
GHC-built Agda in the loop and erases proofs. It could serve as a reference
generator for differential tests, not as a checker.

## Recommendation: staged route

Not selected work. Each stage stands on its own.

| Stage | Content | Exit |
| --- | --- | --- |
| 0 | Executable slice in ordinary Python: terms, substitution, reduction search, machine step, the TM-to-lambda compiler, the demo facts. No checker. | Same output under host CPython and native pcc1 on all five GC backends, compared with the Agda demo's `refl` facts and the C edition's tests |
| 1 | Checker under host CPython for inductive families, `match` with index unification and coverage, structural termination, `Set`/`Set₁`, implicit arguments | Python counterparts of the de Bruijn, reduction and weak-head modules accepted; four negative controls rejected (ill-typed proof, non-covering `match`, non-terminating recursion, non-positive datatype) |
| 2 | `with`, `rewrite`, records, `Fin`, `Vec`, `Dec`, prelude lemmas | Lambda meta-theory through confluence and standardization checked, with a proof map to Agda symbols |
| 3 | The checker compiled by pcc1 | Same accept and reject corpus natively; time and RSS recorded; five GC backends |
| 4 | Machine side: finite tape machines and the TM-to-lambda equivalence | Decide whether proofs by computation need compiled evaluation; if so, label the compiler as trusted for those proofs |
| 5 | Optional: Lean replay kernel; automation; compiler use of proofs (range proofs for the "proven-in-range" raw-integer lane of the value model) | separate proposals |

Stop signals:

- Stage 1 cannot express index unification for a family like `Step` in valid
  Python without relying on eager-evaluation accidents. Then reconsider the
  surface.
- Host-CPython checking of the Stage 2 corpus is too slow to iterate. Then go
  native first.
- Porting the lemma closure of the standard library dominates the work. Then
  cut the prelude to a monomorphic core.

Cost, as an unmeasured judgment: a checker for this feature set is on the
order of ten thousand lines of Python plus the prelude. Most of the difficulty
is in implicit-argument inference and `with`-abstraction, which the corpus
uses too heavily to skip. The main soundness risk is the size of the trusted
checker; the arena's reject scores for young kernels show how commonly a new
checker accepts wrong proofs, so the negative corpus has to exist from Stage 1.

## Decisions that belong to the maintainer

1. First corpus: the Agda edition, or Lean replay.
2. Surface: one runnable Python source with two readings, or proofs in a
   separate non-executable notation.
3. Foundations: constructive and axiom-free like the `--safe` corpus, or a
   labeled classical mode for the Lean capstones.
4. Trust model: Agda-style trusted pattern matching, or a smaller eliminator
   core underneath.
5. Location: inside pcc with a public command, or a sibling project that pcc
   compiles until Stage 3 shows the native checker working.

## Evidence boundary

- Read: this repository at `4e2feca72`; the Lean edition at `5474f4f`; the
  Agda edition at `7aaed61`.
- Executed: the syntax sketch under host CPython 3.15.0; tool version queries.
- Not executed: any pcc compilation, any Agda or Lean check, any export.
- External facts are search summaries from 2026-11-12 and are marked where
  they were not read first-hand.

[arena]: https://arena.lean-lang.org/
[rpylean]: https://arena.lean-lang.org/checker/rpylean/
