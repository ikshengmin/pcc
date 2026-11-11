"""The trusted kernel: what it accepts and, above all, what it rejects.

Every declaration here is written directly in core terms, so these tests do
not depend on the Python surface or on the elaborator.
"""

from __future__ import annotations

import pytest

from pcc.proof.kernel import Context, Kernel, KernelError
from pcc.proof.terms import Leaf, Meta, Sort

from tests.proof.kernel_dsl import (
    A,
    C,
    L,
    TYPE,
    TYPE1,
    V,
    core,
    data,
    fn,
    function,
    leaf,
    split,
    with_,
)

NAT = C("Nat")
ZERO = C("zero")


def suc(term):
    return A(C("suc"), term)


def numeral(count):
    term = ZERO
    for _ in range(count):
        term = suc(term)
    return term


def eq(type_, left, right):
    return A(C("Eq"), type_, left, right)


def refl(type_, value):
    return A(C("refl"), type_, value)


def base_kernel():
    """Naturals, booleans, equality, the empty type and addition."""
    kernel = Kernel()
    data(kernel, "Nat", [], [], [("zero", [], []), ("suc", [("n", NAT)], [])])
    data(kernel, "Bool", [], [], [("false", [], []), ("true", [], [])])
    data(kernel, "Empty", [], [], [])
    data(
        kernel,
        "Eq",
        [("A", TYPE), ("x", V("A"))],
        [("y", V("A"))],
        [("refl", [], [V("x")])],
    )
    function(
        kernel,
        "add",
        [("n", NAT), ("m", NAT)],
        NAT,
        split(
            "m",
            ("zero", [], leaf(V("n"))),
            ("suc", ["k"], leaf(suc(A(C("add"), V("n"), V("k"))))),
        ),
    )
    return kernel


def convertible(kernel, left, right):
    machine = kernel.machine(Context())
    return machine.conv(
        machine.evaluate(core(left), ()), machine.evaluate(core(right), ()), 0
    )


# -- accepted declarations ---------------------------------------------------

def test_addition_computes():
    kernel = base_kernel()
    assert convertible(kernel, A(C("add"), numeral(2), numeral(3)), numeral(5))
    assert not convertible(kernel, A(C("add"), numeral(2), numeral(3)), numeral(4))


def test_addition_is_stuck_on_a_variable_and_resumes():
    kernel = base_kernel()
    ctx = Context().extend("n", kernel.machine(Context()).evaluate(core(NAT), ()))
    machine = kernel.machine(ctx)
    stuck = machine.evaluate(core(A(C("add"), ZERO, V("n")), ["n"]), ctx.env())
    assert not machine.conv(stuck, ctx.env()[0], 1)
    refined = ctx.define(0, machine.evaluate(core(numeral(2)), ()))
    resumed = kernel.machine(refined)
    assert resumed.conv(stuck, resumed.evaluate(core(numeral(2)), ()), 1)


def test_symmetry_of_equality_by_matching_on_refl():
    kernel = base_kernel()
    function(
        kernel,
        "sym",
        [("A", TYPE), ("x", V("A")), ("y", V("A")), ("e", eq(V("A"), V("x"), V("y")))],
        eq(V("A"), V("y"), V("x")),
        split("e", ("refl", [], leaf(refl(V("A"), V("x"))))),
    )


def test_induction_proves_zero_is_a_left_unit():
    kernel = base_kernel()
    function(
        kernel,
        "cong_suc",
        [("a", NAT), ("b", NAT), ("e", eq(NAT, V("a"), V("b")))],
        eq(NAT, suc(V("a")), suc(V("b"))),
        split("e", ("refl", [], leaf(refl(NAT, suc(V("a")))))),
    )
    function(
        kernel,
        "add_zero_left",
        [("n", NAT)],
        eq(NAT, A(C("add"), ZERO, V("n")), V("n")),
        split(
            "n",
            ("zero", [], leaf(refl(NAT, ZERO))),
            (
                "suc",
                ["k"],
                leaf(
                    A(
                        C("cong_suc"),
                        A(C("add"), ZERO, V("k")),
                        V("k"),
                        A(C("add_zero_left"), V("k")),
                    )
                ),
            ),
        ),
    )


def test_empty_type_eliminates_into_anything():
    kernel = base_kernel()
    function(kernel, "absurd", [("A", TYPE), ("e", C("Empty"))], V("A"), split("e"))


def test_impossible_index_needs_no_branch():
    kernel = base_kernel()
    data(kernel, "IsZero", [], [("n", NAT)], [("is_zero", [], [ZERO])])
    function(
        kernel,
        "one_is_not_zero",
        [("p", A(C("IsZero"), numeral(1)))],
        C("Empty"),
        split("p"),
    )


def test_cyclic_equation_is_impossible():
    kernel = base_kernel()
    function(
        kernel,
        "no_fixed_point",
        [("n", NAT), ("p", eq(NAT, V("n"), suc(V("n"))))],
        C("Empty"),
        split("p"),
    )


def test_lexicographic_recursion_is_accepted():
    kernel = base_kernel()
    ack = C("ack")
    function(
        kernel,
        "ack",
        [("m", NAT), ("n", NAT)],
        NAT,
        split(
            "m",
            ("zero", [], leaf(suc(V("n")))),
            (
                "suc",
                ["m1"],
                split(
                    "n",
                    ("zero", [], leaf(A(ack, V("m1"), numeral(1)))),
                    (
                        "suc",
                        ["n1"],
                        leaf(A(ack, V("m1"), A(ack, suc(V("m1")), V("n1")))),
                    ),
                ),
            ),
        ),
    )
    assert convertible(kernel, A(ack, numeral(2), numeral(1)), numeral(5))


def test_abstraction_over_an_expression():
    kernel = base_kernel()
    function(
        kernel,
        "is_zero",
        [("n", NAT)],
        C("Bool"),
        split("n", ("zero", [], leaf(C("true"))), ("suc", ["k"], leaf(C("false")))),
    )
    function(
        kernel,
        "sum_is_zero_or_not",
        [("a", NAT), ("b", NAT)],
        NAT,
        with_(
            A(C("is_zero"), A(C("add"), V("a"), V("b"))),
            "w",
            NAT,
            split("w", ("false", [], leaf(numeral(1))), ("true", [], leaf(ZERO))),
        ),
    )
    assert convertible(kernel, A(C("sum_is_zero_or_not"), ZERO, ZERO), ZERO)
    assert convertible(kernel, A(C("sum_is_zero_or_not"), numeral(2), ZERO), numeral(1))


def test_dependent_abstraction_refines_the_goal():
    """``with`` on ``is_zero n`` lets a goal that mentions it reduce."""
    kernel = base_kernel()
    function(
        kernel,
        "is_zero",
        [("n", NAT)],
        C("Bool"),
        split("n", ("zero", [], leaf(C("true"))), ("suc", ["k"], leaf(C("false")))),
    )
    function(
        kernel,
        "pick",
        [("b", C("Bool"))],
        NAT,
        split("b", ("false", [], leaf(numeral(7))), ("true", [], leaf(ZERO))),
    )
    function(
        kernel,
        "pick_is_small_or_seven",
        [("n", NAT)],
        eq(NAT, A(C("pick"), A(C("is_zero"), V("n"))), A(C("pick"), A(C("is_zero"), V("n")))),
        with_(
            A(C("is_zero"), V("n")),
            "w",
            eq(NAT, A(C("pick"), V("w")), A(C("pick"), V("w"))),
            split(
                "w",
                ("false", [], leaf(refl(NAT, numeral(7)))),
                ("true", [], leaf(refl(NAT, ZERO))),
            ),
        ),
    )


def test_higher_order_recursion_through_a_function_field():
    kernel = base_kernel()
    data(
        kernel,
        "Tree",
        [],
        [],
        [("tip", [], []), ("node", [("children", fn(NAT, C("Tree")))], [])],
    )
    function(
        kernel,
        "leftmost_depth",
        [("t", C("Tree"))],
        NAT,
        split(
            "t",
            ("tip", [], leaf(ZERO)),
            ("node", ["f"], leaf(suc(A(C("leftmost_depth"), A(V("f"), ZERO))))),
        ),
    )


def test_eta_for_functions():
    kernel = base_kernel()
    assert convertible(kernel, L("n", NAT, suc(V("n"))), C("suc"))


# -- rejected declarations ---------------------------------------------------

def test_rejects_a_body_of_the_wrong_type():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="is required"):
        function(kernel, "bad", [("n", NAT)], C("Bool"), leaf(V("n")))
    assert kernel.sig.kind("bad") is None


def test_rejects_a_false_equation():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="is required"):
        function(kernel, "bad", [], eq(NAT, ZERO, numeral(1)), leaf(refl(NAT, ZERO)))


def test_rejects_a_missing_case():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="missing case suc"):
        function(kernel, "bad", [("n", NAT)], NAT, split("n", ("zero", [], leaf(ZERO))))


def test_rejects_a_branch_for_an_impossible_constructor():
    kernel = base_kernel()
    data(kernel, "IsZero", [], [("n", NAT)], [("is_zero", [], [ZERO])])
    with pytest.raises(KernelError, match="impossible"):
        function(
            kernel,
            "bad",
            [("p", A(C("IsZero"), numeral(1)))],
            NAT,
            split("p", ("is_zero", [], leaf(ZERO))),
        )


def test_rejects_an_empty_split_on_an_inhabited_type():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="missing case"):
        function(kernel, "bad", [("n", NAT)], C("Empty"), split("n"))


def test_rejects_a_loop():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="not structurally decreasing"):
        function(kernel, "loop", [("n", NAT)], NAT, leaf(A(C("loop"), V("n"))))


def test_rejects_recursion_on_a_larger_argument():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="not structurally decreasing"):
        function(kernel, "grow", [("n", NAT)], NAT, leaf(A(C("grow"), suc(V("n")))))


def test_rejects_a_proof_of_falsity_by_self_reference():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="cannot refer to itself"):
        function(kernel, "falsity", [], C("Empty"), leaf(C("falsity")))


def test_rejects_an_unapplied_recursive_reference():
    kernel = base_kernel()
    function(
        kernel,
        "call",
        [("f", fn(NAT, NAT)), ("n", NAT)],
        NAT,
        leaf(A(V("f"), V("n"))),
    )
    with pytest.raises(KernelError, match="must be applied to all"):
        function(
            kernel,
            "through",
            [("n", NAT)],
            NAT,
            leaf(A(C("call"), C("through"), V("n"))),
        )


def test_rejects_recursion_that_swaps_arguments_without_descent():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="not structurally decreasing"):
        function(
            kernel,
            "swap",
            [("a", NAT), ("b", NAT)],
            NAT,
            split(
                "a",
                ("zero", [], leaf(ZERO)),
                ("suc", ["k"], leaf(A(C("swap"), suc(V("b")), V("k")))),
            ),
        )


def test_rejects_a_negative_occurrence():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="negatively"):
        data(kernel, "Bad", [], [], [("mk", [("f", fn(C("Bad"), NAT))], [])])
    assert kernel.sig.kind("Bad") is None
    assert kernel.sig.kind("mk") is None


def test_rejects_an_occurrence_under_another_type_constructor():
    kernel = base_kernel()
    data(kernel, "Box", [("A", TYPE)], [], [("box", [("a", V("A"))], [])])
    with pytest.raises(KernelError, match="strictly positively"):
        data(kernel, "Rose", [], [], [("rose", [("kids", A(C("Box"), C("Rose")))], [])])


def test_rejects_a_large_constructor_argument():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="large type"):
        data(kernel, "Code", [], [], [("code", [("A", TYPE)], [])])


def test_rejects_a_large_index():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="large type"):
        data(kernel, "Tagged", [], [("A", TYPE)], [])


def test_rejects_a_data_type_outside_type():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="must end in Type"):
        kernel.add_data("Big", 0, core(TYPE1), [])


def test_rejects_a_constructor_of_another_type():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="must build a value of Odd"):
        kernel.add_data("Odd", 0, core(TYPE), [("odd", core(NAT))])


def test_rejects_type_in_type():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="is required"):
        kernel.add_function("universe", Sort(0), 0, Leaf(Sort(0)))


def test_rejects_a_metavariable():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="metavariable"):
        kernel.add_function("hole", core(NAT), 0, Leaf(Meta(0)))


def test_rejects_an_open_term():
    kernel = base_kernel()
    with pytest.raises(KernelError):
        kernel.add_function("open", core(NAT), 0, Leaf(core(V("n"), ["n"])))


def test_rejects_a_redeclaration():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="already declared"):
        function(kernel, "add", [("n", NAT)], NAT, leaf(V("n")))


def test_rejects_a_split_when_unification_is_stuck():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="cannot decide"):
        function(
            kernel,
            "stuck",
            [("a", NAT), ("b", NAT), ("p", eq(NAT, A(C("add"), V("a"), V("b")), ZERO))],
            NAT,
            split("p", ("refl", [], leaf(ZERO))),
        )


def test_rejects_a_second_split_on_a_determined_variable():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="already determined"):
        function(
            kernel,
            "twice",
            [("n", NAT)],
            NAT,
            split(
                "n",
                ("zero", [], split("n", ("zero", [], leaf(ZERO)))),
                ("suc", ["k"], leaf(ZERO)),
            ),
        )


def test_rejects_an_abstraction_whose_motive_is_not_the_goal():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="is not the goal"):
        function(
            kernel,
            "bad",
            [("n", NAT)],
            NAT,
            with_(V("n"), "w", C("Bool"), leaf(C("true"))),
        )


def test_rejects_an_ill_typed_application():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="is required"):
        function(kernel, "bad", [], NAT, leaf(suc(C("true"))))


def test_rejects_applying_a_non_function():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="is applied but has type"):
        function(kernel, "bad", [], NAT, leaf(A(ZERO, ZERO)))


def test_a_rejected_declaration_leaves_the_signature_unchanged():
    kernel = base_kernel()
    before = (set(kernel.sig.datas), set(kernel.sig.constructors), set(kernel.sig.functions))
    with pytest.raises(KernelError):
        data(
            kernel,
            "Half",
            [],
            [],
            [("good", [], []), ("bad", [("f", fn(C("Half"), NAT))], [])],
        )
    after = (set(kernel.sig.datas), set(kernel.sig.constructors), set(kernel.sig.functions))
    assert before == after


# -- declarations that are being built ---------------------------------------

def test_nothing_else_can_be_declared_while_a_function_is_pending():
    kernel = base_kernel()
    kernel.begin_function("pending", core(fn(NAT, NAT)), 1)
    assert not kernel.idle
    with pytest.raises(KernelError, match="still being declared"):
        function(kernel, "other", [("n", NAT)], NAT, leaf(V("n")))
    with pytest.raises(KernelError, match="still being declared"):
        data(kernel, "Other", [], [], [])
    kernel.abort()
    assert kernel.idle
    assert kernel.sig.kind("pending") is None


def test_a_pending_function_does_not_compute():
    kernel = base_kernel()
    kernel.begin_function("later", core(NAT), 0)
    machine = kernel.machine(Context())
    value = machine.evaluate(core(C("later")), ())
    assert not machine.conv(value, machine.evaluate(core(ZERO), ()), 0)
    kernel.finish_function("later", Leaf(core(ZERO)))
    assert kernel.idle
    assert convertible(kernel, C("later"), ZERO)


def test_a_rejected_body_withdraws_the_announcement():
    kernel = base_kernel()
    kernel.begin_function("wrong", core(NAT), 0)
    with pytest.raises(KernelError, match="is required"):
        kernel.finish_function("wrong", Leaf(core(C("true"))))
    assert kernel.idle
    assert kernel.sig.kind("wrong") is None


def test_finishing_needs_an_announcement():
    kernel = base_kernel()
    with pytest.raises(KernelError, match="was not announced"):
        kernel.finish_function("nobody", Leaf(core(ZERO)))


def test_an_open_family_must_be_closed_before_it_is_declared():
    kernel = base_kernel()
    kernel.open_data("Open", core(TYPE))
    assert kernel.sig.kind("Open") == "data"
    with pytest.raises(KernelError, match="still being declared"):
        function(kernel, "from_open", [("x", C("Open"))], C("Empty"), split("x"))
    kernel.abort()
    assert kernel.sig.kind("Open") is None
    data(kernel, "Open", [], [], [("only", [], [])])
    assert kernel.sig.datas["Open"].constructors == ("only",)
