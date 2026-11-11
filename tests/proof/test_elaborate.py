"""Python sources checked end to end: reader, elaborator and kernel."""

from __future__ import annotations

from tests.proof.support import HEADER_LINES, PRELUDE, check, normal_form, rejects

LAMBDA = '''
class Term(Data):
    """Untyped lambda terms with de Bruijn indices."""

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

def subst(n: Term, j: Nat, t: Term) -> Term:
    match t:
        case Var(i):
            if i == j:
                return n
            if i < j:
                return Var(i)
            return Var(monus(i, 1))
        case Lam(b):
            return Lam(subst(lift(0, n), j + 1, b))
        case App(f, a):
            return App(subst(n, j, f), subst(n, j, a))

class Step[t: Term, u: Term](Data):
    """One step of beta reduction."""

@dataclass(frozen=True)
class Beta[t: Term, u: Term](Step[App(Lam(t), u), subst(u, 0, t)]):
    pass

@dataclass(frozen=True)
class AppL[t1: Term, t1p: Term, t2: Term](Step[App(t1, t2), App(t1p, t2)]):
    step: Step[t1, t1p]

@dataclass(frozen=True)
class AppR[t1: Term, t2: Term, t2p: Term](Step[App(t1, t2), App(t1, t2p)]):
    step: Step[t2, t2p]

@dataclass(frozen=True)
class Under[t: Term, tp: Term](Step[Lam(t), Lam(tp)]):
    step: Step[t, tp]

class Steps[t: Term, u: Term](Data):
    """Zero or more steps."""

@dataclass(frozen=True)
class Done[t: Term](Steps[t, t]):
    pass

@dataclass(frozen=True)
class More[t1: Term, t2: Term, t3: Term](Steps[t1, t3]):
    head: Step[t1, t2]
    tail: Steps[t2, t3]

def steps_trans[t1: Term, t2: Term, t3: Term](
    r1: Steps[t1, t2], r2: Steps[t2, t3]
) -> Steps[t1, t3]:
    match r1:
        case Done():
            return r2
        case More(s, r):
            return More(s, steps_trans(r, r2))

def cong_app_left[t: Term, tp: Term, u: Term](r: Steps[t, tp]) -> Steps[App(t, u), App(tp, u)]:
    match r:
        case Done():
            return Done()
        case More(s, rest):
            return More(AppL(s), cong_app_left(rest))

def cong_lam[t: Term, tp: Term](r: Steps[t, tp]) -> Steps[Lam(t), Lam(tp)]:
    match r:
        case Done():
            return Done()
        case More(s, rest):
            return More(Under(s), cong_lam(rest))
'''


# -- what the prelude declares -----------------------------------------------

def test_prelude_declarations_reach_the_kernel_as_intended(tmp_path):
    session = check(tmp_path, "ZERO: Nat = 0\n")
    sig = session.kernel.sig
    equality = sig.datas[f"{PRELUDE}.Eq"]
    assert (equality.nparams, equality.nindices) == (2, 1)
    assert equality.constructors == (f"{PRELUDE}.Refl",)
    exists = sig.datas[f"{PRELUDE}.Exists"]
    assert (exists.nparams, exists.nindices) == (2, 0)
    lists = sig.datas[f"{PRELUDE}.List"]
    assert (lists.nparams, lists.nindices) == (1, 0)
    assert sig.datas[f"{PRELUDE}.Empty"].constructors == ()
    assert session.operators["+"] == f"{PRELUDE}.add"
    assert sig.functions[f"{PRELUDE}.add"].arity == 2
    assert sig.functions[f"{PRELUDE}.sym"].arity == 4


def test_arithmetic_computes_in_the_kernel(tmp_path):
    session = check(
        tmp_path,
        """
        SUM: Nat = 2 + 3
        PRODUCT: Nat = 3 * 4
        LESS: Bool = 2 < 3
        NOT_LESS: Bool = 3 < 3
        AT_MOST: Bool = 3 <= 3
        GREATER: Bool = 4 > 3
        SAME: Bool = 5 == 5
        DIFFERENT: Bool = 5 != 5
        BOTH: Bool = 1 < 2 and not 2 < 1
        EITHER: Bool = 2 < 1 or 1 < 2
        CHOICE: Nat = 7 if 1 < 2 else 9
        TRUNCATED: Nat = monus(2, 5)
        """,
    )
    values = {
        name: normal_form(session, "sample", name)
        for name in (
            "SUM", "PRODUCT", "LESS", "NOT_LESS", "AT_MOST", "GREATER", "SAME",
            "DIFFERENT", "BOTH", "EITHER", "CHOICE", "TRUNCATED",
        )
    }
    assert values == {
        "SUM": "5", "PRODUCT": "12", "LESS": "true", "NOT_LESS": "false",
        "AT_MOST": "true", "GREATER": "true", "SAME": "true", "DIFFERENT": "false",
        "BOTH": "true", "EITHER": "true", "CHOICE": "7", "TRUNCATED": "0",
    }


# -- the lambda calculus fragment --------------------------------------------

def test_lambda_calculus_definitions_and_proofs_check(tmp_path):
    session = check(
        tmp_path,
        LAMBDA
        + """
IDENTITY: Term = Lam(Var(0))
LIFTED: Term = lift(0, App(Var(0), Lam(Var(1))))
SUBSTITUTED: Term = subst(IDENTITY, 0, App(Var(0), Var(1)))
""",
    )
    sig = session.kernel.sig
    step = sig.datas["sample.Step"]
    assert (step.nparams, step.nindices) == (0, 2)
    steps = sig.datas["sample.Steps"]
    assert (steps.nparams, steps.nindices) == (0, 2)
    assert normal_form(session, "sample", "LIFTED") == "App(Var(1), Lam(Var(2)))"
    assert normal_form(session, "sample", "SUBSTITUTED") == "App(Lam(Var(0)), Var(0))"


def test_a_proof_by_computation(tmp_path):
    check(
        tmp_path,
        LAMBDA
        + """
def lift_example() -> Eq[lift(0, App(Var(0), Lam(Var(1)))), App(Var(1), Lam(Var(2)))]:
    return Refl()

def beta_example() -> Step[App(Lam(Var(0)), Var(3)), Var(3)]:
    return Beta()
""",
    )


def test_induction_on_naturals(tmp_path):
    check(
        tmp_path,
        """
        def cong_suc[a: Nat, b: Nat](e: Eq[a, b]) -> Eq[suc(a), suc(b)]:
            match e:
                case Refl():
                    return Refl()

        def zero_add(n: Nat) -> Eq[0 + n, n]:
            match n:
                case 0:
                    return Refl()
                case _:
                    return cong_suc(zero_add(n - 1))

        def suc_add(m: Nat, n: Nat) -> Eq[suc(m) + n, suc(m + n)]:
            match n:
                case 0:
                    return Refl()
                case _:
                    return cong_suc(suc_add(m, n - 1))

        def add_comm(m: Nat, n: Nat) -> Eq[m + n, n + m]:
            match n:
                case 0:
                    return sym(zero_add(m))
                case _:
                    return trans(cong_suc(add_comm(m, n - 1)), sym(suc_add(n - 1, m)))
        """,
    )


def test_matching_two_arguments_at_once(tmp_path):
    session = check(
        tmp_path,
        """
        def both_zero(a: Nat, b: Nat) -> Bool:
            match a, b:
                case (0, 0):
                    return True
                case _:
                    return False

        def minimum(a: Nat, b: Nat) -> Nat:
            match a, b:
                case (0, _):
                    return 0
                case (_, 0):
                    return 0
                case _:
                    return suc(minimum(a - 1, b - 1))

        YES: Bool = both_zero(0, 0)
        NO: Bool = both_zero(0, 2)
        MIN: Nat = minimum(5, 3)
        """,
    )
    assert normal_form(session, "sample", "YES") == "true"
    assert normal_form(session, "sample", "NO") == "false"
    assert normal_form(session, "sample", "MIN") == "3"


def test_nested_patterns_and_as_bindings(tmp_path):
    session = check(
        tmp_path,
        """
        def second_or_zero(xs: List[Nat]) -> Nat:
            match xs:
                case Cons(_, Cons(y, _)):
                    return y
                case _:
                    return 0

        def keep_if_pair(xs: List[Nat]) -> List[Nat]:
            match xs:
                case Cons(_, Cons(_, Nil())) as whole:
                    return whole
                case _:
                    return Nil()

        PICKED: Nat = second_or_zero(Cons(4, Cons(7, Nil())))
        MISSED: Nat = second_or_zero(Cons(4, Nil()))
        KEPT: List[Nat] = keep_if_pair(Cons(1, Cons(2, Nil())))
        """,
    )
    assert normal_form(session, "sample", "PICKED") == "7"
    assert normal_form(session, "sample", "MISSED") == "0"
    assert normal_form(session, "sample", "KEPT") == "Cons(Nat, 1, Cons(Nat, 2, Nil(Nat)))"


def test_impossible_cases_may_be_left_out(tmp_path):
    check(
        tmp_path,
        """
        class Vec[n: Nat](Data): pass

        @dataclass(frozen=True)
        class VNil(Vec[0]): pass

        @dataclass(frozen=True)
        class VCons[n: Nat](Vec[suc(n)]):
            head: Nat
            tail: Vec[n]

        def first[n: Nat](v: Vec[suc(n)]) -> Nat:
            match v:
                case VCons(h, _):
                    return h

        def zip_add[n: Nat](a: Vec[n], b: Vec[n]) -> Vec[n]:
            match a, b:
                case (VNil(), VNil()):
                    return VNil()
                case (VCons(x, xs), VCons(y, ys)):
                    return VCons(x + y, zip_add(xs, ys))

        def one_is_not_zero(e: Eq[1, 0]) -> Empty:
            return impossible(e)

        def no_fixed_point(n: Nat, e: Eq[n, suc(n)]) -> Empty:
            return impossible(e)
        """,
    )


def test_a_missing_case_is_inferred_absurd_one_split_deeper(tmp_path):
    check(
        tmp_path,
        """
        class IsZero[n: Nat](Data): pass

        @dataclass(frozen=True)
        class Zeroed(IsZero[0]): pass

        def only_zero(n: Nat, p: IsZero[n]) -> Eq[n, 0]:
            match n:
                case 0:
                    return Refl()
        """,
    )


def test_matching_on_an_expression_generalizes_the_goal(tmp_path):
    check(
        tmp_path,
        LAMBDA
        + """
def lift_of_variable_agrees_with_its_test(c: Nat, i: Nat) -> Eq[
    lift(c, Var(i)), Var(i) if i < c else Var(i + 1)
]:
    match i < c:
        case True:
            return Refl()
        case False:
            return Refl()

def lift_of_variable_is_a_variable(c: Nat, i: Nat) -> Exists[Nat, lambda j: Eq[lift(c, Var(i)), Var(j)]]:
    if i < c:
        return Pair(i, Refl())
    return Pair(i + 1, Refl())
""",
    )


def test_functions_as_values_and_dependent_pairs(tmp_path):
    session = check(
        tmp_path,
        """
        type Rename = Fn[Nat, Nat]

        def ext(rho: Rename) -> Rename:
            return lambda x: 0 if x == 0 else suc(rho(monus(x, 1)))

        def twice(f: Rename) -> Rename:
            return lambda n: f(f(n))

        def has_predecessor(n: Nat) -> Exists[Nat, lambda m: Eq[suc(m), suc(n)]]:
            return Pair(n, Refl())

        def apply_twice(n: Nat) -> Nat:
            return twice(suc)(n)

        FOUR: Nat = apply_twice(2)
        SHIFTED: Nat = ext(suc)(3)
        """,
    )
    assert normal_form(session, "sample", "FOUR") == "4"
    assert normal_form(session, "sample", "SHIFTED") == "4"


def test_explicit_type_arguments_on_a_constructor(tmp_path):
    check(
        tmp_path,
        """
        EMPTY: List[Nat] = Nil[Nat]()
        ONE: Maybe[Nat] = Just[Nat](1)
        """,
    )


def test_type_level_definitions(tmp_path):
    check(
        tmp_path,
        """
        def Positive(n: Nat) -> Type:
            return Eq[0 < n, True]

        def three_is_positive() -> Positive(3):
            return Refl()

        def NoProof(P: Type) -> Type:
            return Not[P]

        def zero_is_not_positive() -> NoProof(Positive(0)):
            return lambda p: impossible_equation(p)

        def impossible_equation(p: Eq[False, True]) -> Empty:
            return impossible(p)
        """.replace(
            # The helper must be declared before its use.
            """        def zero_is_not_positive() -> NoProof(Positive(0)):
            return lambda p: impossible_equation(p)

        def impossible_equation(p: Eq[False, True]) -> Empty:
            return impossible(p)
""",
            """        def impossible_equation(p: Eq[False, True]) -> Empty:
            return impossible(p)

        def zero_is_not_positive() -> NoProof(Positive(0)):
            return lambda p: impossible_equation(p)
""",
        ),
    )


def test_modules_import_each_other(tmp_path):
    from tests.proof.support import write_module

    write_module(
        tmp_path,
        """
        def double(n: Nat) -> Nat:
            return n + n
        """,
        name="library",
    )
    session = check(
        tmp_path,
        """
        from library import double

        EIGHT: Nat = double(4)
        """,
        name="client",
    )
    assert normal_form(session, "client", "EIGHT") == "8"
    assert set(session.reports) >= {"library", "client", PRELUDE}


# -- rejected sources --------------------------------------------------------

def test_rejects_a_false_equation(tmp_path):
    error = rejects(
        tmp_path,
        """
        def wrong() -> Eq[1 + 1, 3]:
            return Refl()
        """,
        "is required",
    )
    assert error.line == HEADER_LINES + 2


def test_rejects_a_wrong_reduction_step(tmp_path):
    rejects(
        tmp_path,
        LAMBDA
        + """
def wrong() -> Step[App(Lam(Var(0)), Var(3)), Var(4)]:
    return Beta()
""",
        "is required",
    )


def test_rejects_a_match_that_misses_a_case(tmp_path):
    rejects(
        tmp_path,
        """
        def partial(n: Nat) -> Nat:
            match n:
                case 0:
                    return 0
        """,
        "does not cover the case suc",
    )


def test_rejects_a_missing_constructor_case(tmp_path):
    rejects(
        tmp_path,
        LAMBDA
        + """
def size(t: Term) -> Nat:
    match t:
        case Var(i):
            return 1
        case Lam(b):
            return suc(size(b))
""",
        "does not cover the case App",
    )


def test_rejects_a_case_that_cannot_happen(tmp_path):
    rejects(
        tmp_path,
        """
        class IsZero[n: Nat](Data): pass

        @dataclass(frozen=True)
        class Zeroed(IsZero[0]): pass

        def bad(p: IsZero[1]) -> Nat:
            match p:
                case Zeroed():
                    return 0
        """,
        "case Zeroed is impossible here",
    )


def test_rejects_a_loop(tmp_path):
    rejects(
        tmp_path,
        """
        def loop(n: Nat) -> Nat:
            return loop(n)
        """,
        "not structurally decreasing",
    )


def test_rejects_a_proof_of_falsity_by_a_loop(tmp_path):
    rejects(
        tmp_path,
        """
        def falsity(n: Nat) -> Empty:
            return falsity(suc(n))
        """,
        "not structurally decreasing",
    )


def test_rejects_impossible_on_a_possible_case(tmp_path):
    rejects(
        tmp_path,
        """
        def cheat(n: Nat) -> Empty:
            return impossible(n)
        """,
        "n is not impossible: it can be zero",
    )


def test_rejects_subtraction_that_could_go_negative(tmp_path):
    rejects(
        tmp_path,
        """
        def previous(n: Nat) -> Nat:
            return n - 1
        """,
        "known to be at least 1",
    )


def test_rejects_a_lambda_with_two_parameters(tmp_path):
    rejects(
        tmp_path,
        """
        def pair_adder() -> Fn[Nat, Nat, Nat]:
            return lambda a, b: a + b
        """,
        "exactly one parameter",
    )


def test_rejects_a_two_parameter_function_as_a_value(tmp_path):
    rejects(
        tmp_path,
        """
        def adder() -> Fn[Nat, Nat, Nat]:
            return add
        """,
        "function add is not a value by itself",
    )


def test_rejects_a_partial_call(tmp_path):
    rejects(
        tmp_path,
        """
        def increment() -> Fn[Nat, Nat]:
            return add(1)
        """,
        "add takes 2 arguments but 1 are given",
    )


def test_rejects_a_constructor_class_used_as_a_value(tmp_path):
    rejects(
        tmp_path,
        """
        NOTHING: Maybe[Nat] = Nothing
        """,
        "write Nothing\\(\\)",
    )


def test_rejects_an_undetermined_implicit_argument(tmp_path):
    rejects(
        tmp_path,
        """
        def empty_length() -> Nat:
            match Nil():
                case Nil():
                    return 0
                case Cons(_, _):
                    return 1
        """,
        "cannot infer",
    )


def test_rejects_a_negative_data_type(tmp_path):
    rejects(
        tmp_path,
        """
        class Bad(Data): pass

        @dataclass(frozen=True)
        class MkBad(Bad):
            run: Fn[Bad, Nat]
        """,
        "occurs negatively",
    )


def test_rejects_a_type_stored_in_a_small_data_type(tmp_path):
    rejects(
        tmp_path,
        """
        class Code(Data): pass

        @dataclass(frozen=True)
        class MkCode(Code):
            carrier: Type
        """,
        "large type",
    )


def test_rejects_a_constructor_away_from_its_family(tmp_path):
    rejects(
        tmp_path,
        """
        class Colour(Data): pass

        ZERO: Nat = 0

        @dataclass(frozen=True)
        class Red(Colour): pass
        """,
        "must directly follow its family",
    )


def test_rejects_an_unknown_name(tmp_path):
    rejects(
        tmp_path,
        """
        def f(n: Nat) -> Nat:
            return undefined_thing(n)
        """,
        "undefined_thing is not defined",
    )


def test_rejects_a_redefinition(tmp_path):
    rejects(
        tmp_path,
        """
        ZERO: Nat = 0
        ZERO: Nat = 1
        """,
        "already defined",
    )


def test_rejects_a_wrong_argument_type(tmp_path):
    rejects(
        tmp_path,
        """
        def f(b: Bool) -> Nat:
            return suc(b)
        """,
        "is required",
    )


def test_rejects_a_pattern_of_another_type(tmp_path):
    rejects(
        tmp_path,
        """
        def f(n: Nat) -> Nat:
            match n:
                case Nil():
                    return 0
                case _:
                    return 1
        """,
        "Nil is not a constructor of Nat",
    )


def test_rejects_statements_after_a_return(tmp_path):
    rejects(
        tmp_path,
        """
        def f(n: Nat) -> Nat:
            return n
            return 0
        """,
        "can never run",
    )


def test_rejects_a_path_without_a_return(tmp_path):
    rejects(
        tmp_path,
        """
        def f(b: Bool) -> Nat:
            if b:
                return 1
        """,
        "does not return a value",
    )


def test_rejects_an_import_of_a_missing_name(tmp_path):
    rejects(
        tmp_path,
        """
        from pcc.proof.prelude import no_such_lemma
        """,
        "does not define no_such_lemma",
    )


def test_rejects_a_missing_module(tmp_path):
    rejects(
        tmp_path,
        """
        from no_such_module import thing
        """,
        "cannot find module no_such_module",
    )


def test_rejects_an_operator_at_the_wrong_type(tmp_path):
    rejects(
        tmp_path,
        """
        def f(b: Bool) -> Nat:
            return b + 1
        """,
        "is required",
    )
