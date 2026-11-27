"""Pass statistics have both integer counters and floating-point timings."""

import ast
import inspect
import textwrap
from typing import get_type_hints

from pcc.frontends.c.passes import PassContext, PassPipeline
from pcc.frontends.c.passes import base
from pcc.frontends.c.passes.base import ASTPass, IRPass


def test_pass_context_stats_annotations_admit_integer_and_float():
    # The native frontend uses these annotations to choose integer-only or
    # mixed-object arithmetic. Passing a float to an int-only kernel returns
    # NULL instead of recording the timing value.
    assert get_type_hints(PassContext.bump)["count"] == int | float
    init = ast.parse(textwrap.dedent(inspect.getsource(PassContext.__init__)))
    stats = [
        node.annotation
        for node in ast.walk(init)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Attribute)
        and isinstance(node.target.value, ast.Name)
        and node.target.value.id == "self"
        and node.target.attr == "stats"
    ]
    assert len(stats) == 1
    assert ast.dump(stats[0]) == ast.dump(ast.parse("dict[str, int | float]", mode="eval").body)


def test_pass_context_keeps_mixed_stats_and_none_return():
    ctx = PassContext(opt_level=0)
    assert ctx.bump("counter") is None
    bound = ctx.bump
    assert bound("counter", 3) is None
    assert ctx.bump("elapsed", 0.125) is None
    assert bound("elapsed", 0.125) is None
    assert bound("both", 2) is None
    assert ctx.bump("both", 0.125) is None
    assert bound("both", 1) is None
    assert ctx.bump("big", 1 << 100) is None
    expected = {
        "counter": 4,
        "elapsed": 0.25,
        "both": 3.125,
        "big": 1 << 100,
    }
    assert ctx.stats == expected
    assert {k: type(v) for k, v in ctx.stats.items()} == {
        k: type(v) for k, v in expected.items()
    }
    restored = PassContext.from_pass_report(ctx.pass_report())
    assert restored.stats == ctx.stats
    assert restored.dump_stats() == ctx.dump_stats()


class CountHigh(ASTPass):
    name = "mixed-high"

    def run(self, ast_node, ctx):
        ctx.bump("high-counter")


class CountLow(IRPass):
    name = "mixed-low"

    def run(self, ir_text, ctx):
        ctx.bump("low-counter")
        return ir_text


def test_real_pipeline_records_integer_counts_and_fractional_timings(monkeypatch):
    ticks = iter((1.0, 1.000125, 2.0, 2.00025))
    monkeypatch.setattr(base.time, "monotonic", lambda: next(ticks))
    ctx = PassContext(opt_level=0)
    pipeline = PassPipeline()
    pipeline.add_high(CountHigh())
    pipeline.add_low(CountLow())
    node = object()
    assert pipeline.run_high_tier(node, ctx) is node
    assert pipeline.run_low_tier("unchanged", ctx) == "unchanged"
    assert ctx.stats == {
        "high-counter": 1, "pass.mixed-high.time_ms": 0.125,
        "low-counter": 1, "pass.mixed-low.time_ms": 0.25,
    }
    assert type(ctx.stats["high-counter"]) is int
    assert type(ctx.stats["low-counter"]) is int
    assert type(ctx.stats["pass.mixed-high.time_ms"]) is float
    assert type(ctx.stats["pass.mixed-low.time_ms"]) is float
    for name, expected in (("mixed-high", 0.125), ("mixed-low", 0.25)):
        assert ctx.pass_metrics[name].runs == 1
        assert ctx.pass_metrics[name].total_time_ms == expected
