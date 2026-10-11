"""Finite stdlib/fake-CFG facts controls; no PCC imports, parse or emission."""

import ast
import hashlib
from pathlib import Path
import random

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "pcc/backend/self_backend_aarch64_darwin_regalloc.py"
ORACLE = ROOT / "tests/aarch64_regalloc_facts_reference.py"
ORACLE_FUNCTION_SHA256 = "c3233aa65b468c080334c5871d63899ec34e1d21b92e6495016e4c2c2bf2fca6"


class Record:
    def __init__(self, first=-1, second=0, third=0, fourth=-1):
        self.first, self.second, self.third, self.fourth = first, second, third, fourth


def case(edges, uses=None, homes=(), phis=None, errors=None, kinds=None, terms=None):
    uses, phis, errors, kinds, terms = uses or {}, phis or {}, errors or {}, kinds or {}, terms or {}
    blocks = [dict(instructions=[], edges=list(targets), phis=list(phis.get(index, ())),
                   errors=list(errors.get(index, ())), terms=list(terms.get(index, ())))
              for index, targets in enumerate(edges)]
    for value, home in homes:
        blocks[home]["instructions"].append((1, value, []))
    for index, values in uses.items():
        for value in values:
            blocks[index]["instructions"].append((kinds.get(index, 1), -1, [value]))
    return blocks


class FakeKernel:
    def __init__(self, blocks, fail=None, events=None):
        self.block_names = ["b" + str(index) for index in range(len(blocks))]
        self.blocks, self.fail, self.events = blocks, fail, events if events is not None else []
        self.instructions, self.facts, self.phis, self.incoming = [], [], [], []
        self.phi_facts, self.cases, self.spans, self.error_targets, self.error_spans = [], [], [], [], []
        for block in blocks:
            self.facts.append(Record(len(self.instructions), len(block["instructions"])))
            self.instructions.extend(block["instructions"])
            self.phi_facts.append(Record(len(self.phis), len(block["phis"])))
            for value, incoming in block["phis"]:
                self.phis.append(Record(value, 0, len(self.incoming), len(incoming)))
                self.incoming.extend(Record(value, predecessor) for value, predecessor in incoming)
            edges = block["edges"]
            self.spans.append(Record(edges[1] if len(edges) > 1 else -1,
                                     len(self.cases), max(0, len(edges) - 2)))
            self.cases.extend(Record(0, target) for target in edges[2:])
            self.error_spans.append(Record(len(self.error_targets), len(block["errors"])))
            self.error_targets.extend(block["errors"])

    def read(self, name, *args):
        self.events.append((name, args))
        if self.fail is not None and name == self.fail[0]:
            raise self.fail[1]("fake " + name)

    def block_fact(self, block):
        self.read("block_fact", block)
        return self.facts[block]

    def instruction_metadata_by_id(self, instruction):
        self.read("instruction_metadata_by_id", instruction)
        return Record(self.instructions[instruction][0])

    def instruction_fact_by_id(self, instruction):
        self.read("instruction_fact_by_id", instruction)
        return Record(self.instructions[instruction][1])

    def instruction_use_count(self, block, instruction):
        self.read("instruction_use_count", block, instruction)
        return len(self.blocks[block]["instructions"][instruction][2])

    def instruction_use_id(self, block, instruction, use):
        self.read("instruction_use_id", block, instruction, use)
        return self.blocks[block]["instructions"][instruction][2][use]

    def terminator_use_id(self, block, use):
        self.read("terminator_use_id", block, use)
        return self.blocks[block]["terms"][use]

    def block_phi_fact(self, block):
        self.read("block_phi_fact", block)
        return self.phi_facts[block]

    def phi_record(self, phi):
        self.read("phi_record", phi)
        return self.phis[phi]

    def phi_incoming(self, incoming):
        self.read("phi_incoming", incoming)
        return self.incoming[incoming]

    def terminator_header(self, block):
        self.read("terminator_header", block)
        edges = self.blocks[block]["edges"]
        return Record(fourth=edges[0] if edges else -1)

    def terminator_span(self, block):
        self.read("terminator_span", block)
        return self.spans[block]

    def terminator_case(self, index):
        self.read("terminator_case", index)
        return self.cases[index]

    def inline_error_edge_span(self, block):
        self.read("inline_error_edge_span", block)
        return self.error_spans[block]

    def inline_error_edge_target(self, index):
        self.read("inline_error_edge_target", index)
        return self.error_targets[index]


def load_facts(path, *, count=False):
    source = ast.parse(path.read_text())
    production = ast.parse(SOURCE.read_text())
    helper = next(node for node in production.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_note_use_block")
    function = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_function_level_facts")
    if path == ORACLE:
        frozen = ast.get_source_segment(path.read_text(), function)
        assert hashlib.sha256(frozen.encode()).hexdigest() == ORACLE_FUNCTION_SHA256
    counts = dict(pops=0, edges=0, hits=0, writes=0)

    class Count(ast.NodeTransformer):
        pops = 0
        edges = 0

        def bump(self, key):
            return ast.parse("_counts[" + repr(key) + "] += 1").body[0]

        def visit_While(self, node):
            self.generic_visit(node)
            if isinstance(node.test, ast.Name) and node.test.id == "pending":
                node.body.insert(0, self.bump("pops"))
                self.pops += 1
            return node

        def visit_For(self, node):
            self.generic_visit(node)
            if ast.unparse(node.target) == "predecessor" and ast.unparse(node.iter) == "predecessors[block_id]":
                node.body.insert(0, self.bump("edges"))
                self.edges += 1
            return node

        def visit_If(self, node):
            self.generic_visit(node)
            if ast.unparse(node.test) == "cached_low >= 0":
                node.body.insert(0, self.bump("hits"))
            if isinstance(node.test, ast.Name) and node.test.id == "cache_miss":
                node.body.insert(0, self.bump("writes"))
            return node

    if count:
        counter = Count()
        function = counter.visit(function)
        assert (counter.pops, counter.edges) == (1, 1)
    tree = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                           helper, function], type_ignores=[])
    namespace = dict(CompilerInt4=Record, IndexedFunctionKernel=type("ExactKernel", (), {}),
                     PARSED_INSTRUCTION_KINDS=tuple(range(16)),
                     PARSED_INSTRUCTION_KIND_CALL=2, _POOL_REJECTED_INSTRUCTION_KIND_IDS=(14, 15),
                     _counts=counts)
    exec(compile(ast.fix_missing_locations(tree), str(path), "exec"), namespace)
    return namespace["_function_level_facts"], counts


def evaluate(function, kernel):
    try:
        values = function(kernel)
        assert len(values) == 7
        return ("value", tuple(tuple(value.items()) if isinstance(value, dict) else tuple(value)
                               for value in values))
    except Exception as error:
        return ("error", type(error), error.args)


def compare(builder, *, count=False, functions=None):
    if functions is None:
        expected, old_counts = load_facts(ORACLE, count=count)
        actual, new_counts = load_facts(SOURCE, count=count)
    else:
        expected, actual = functions
        old_counts, new_counts = {}, {}
    old_kernel, new_kernel = builder(), builder()
    old_result, new_result = evaluate(expected, old_kernel), evaluate(actual, new_kernel)
    assert new_kernel.events == old_kernel.events
    assert new_result == old_result
    return new_result, old_counts, new_counts


@pytest.mark.parametrize("seed", (0, 1, 71, 1729))
def test_random_small_cfg_matches_frozen_facts(seed):
    randomizer = random.Random(seed)
    functions = (load_facts(ORACLE)[0], load_facts(SOURCE)[0])
    for _ in range(128):
        size = randomizer.randrange(8)
        edges = [[randomizer.randrange(-2, size + 3) for _ in range(randomizer.randrange(5))]
                 for _ in range(size)]
        uses = {block: [randomizer.randrange(-2, 10) for _ in range(randomizer.randrange(6))]
                for block in range(size)}
        homes = [(value, randomizer.randrange(size)) for value in range(8)
                 if size and randomizer.randrange(2)]
        phis = {block: [(randomizer.randrange(10), [(randomizer.randrange(-2, 10),
                  randomizer.randrange(-2, size + 2)) for _ in range(3)])]
                for block in range(size) if randomizer.randrange(3) == 0}
        errors = {block: [randomizer.randrange(-2, size + 2) for _ in range(2)]
                  for block in range(size)}
        kinds = {block: randomizer.choice((-1, 1, 2, 14, 16)) for block in range(size)}
        blocks = case(edges, uses, homes, phis, errors, kinds)
        compare(lambda: FakeKernel(blocks), functions=functions)


@pytest.mark.parametrize("shape", ("empty", "home-only", "undefined", "duplicate-edges",
                                    "home-cut-cycle", "permuted-layout", "phi-home", "error-barrier"))
def test_named_cfg_contracts(shape):
    if shape == "empty":
        blocks = []
    elif shape == "home-only":
        blocks = case([[]], {0: [0, 1]}, ((0, 0), (1, 0)))
    elif shape == "undefined":
        blocks = case([[1], []], {1: [-2, 0, 1]})
    elif shape == "duplicate-edges":
        blocks = case([[1, 1, 1, -1, 99], [1]], {1: [0, 1, 2]})
    elif shape == "home-cut-cycle":
        blocks = case([[1, 2], [0], [0, 3], [2]], {1: [0, 1], 3: [2, 3]},
                      ((0, 0), (1, 0), (2, 0), (3, 0)))
    elif shape == "permuted-layout":
        blocks = case([[3], [0], [1], [2]], {0: [0, 1]}, ((0, 2), (1, 2)))
    elif shape == "phi-home":
        blocks = case([[1], [2], []], {2: [0, 1]}, ((0, 0), (1, 0)),
                      {1: [(0, [(2, 0)]), (1, [(3, 0)])]})
    else:
        blocks = case([[], [2], [1]], {1: [0, 1], 2: [2, 3]}, errors={0: [1, 1, 99]},
                      kinds={0: 14, 1: 2, 2: 16})
    compare(lambda: FakeKernel(blocks))


@pytest.mark.parametrize("position", (0, 1, 3))
def test_minus_one_retains_whole_old_stamp_history(position):
    values = [0, 1, 2]
    values.insert(position, -1)
    blocks = case([[1], [2], [1]], {2: values})
    _, old, new = compare(lambda: FakeKernel(blocks), count=True)
    assert new == old and new["hits"] == 0


@pytest.mark.parametrize("kind", ("custom-use", "custom-definition", "bool", "float", "custom-phi-seed"))
def test_custom_protocols_keep_original_calls(kind):
    def build():
        events = []

        class CustomInt(int):
            def __hash__(self):
                events.append(("hash", int(self)))
                return int.__hash__(self)

            def __eq__(self, other):
                events.append(("eq", int(self), int(other)))
                return int.__eq__(self, other)

            def __ne__(self, other):
                events.append(("ne", int(self), int(other)))
                return int.__ne__(self, other)

        if kind == "custom-use":
            blocks = case([[1], []], {1: [0, CustomInt(1), 2]})
        elif kind == "custom-definition":
            blocks = case([[1], []], {1: [0, 1]}, ((CustomInt(0), 0),))
        elif kind == "bool":
            blocks = case([[1], []], {1: [0, True, 2]})
        elif kind == "float":
            blocks = case([[1], []], {1: [0, 1]}, ((0.0, 0),))
        else:
            blocks = case([[1], []], {1: [0, 1]}, phis={1: [(2, [(0, CustomInt(0)), (1, CustomInt(0))])]})
        return FakeKernel(blocks, events=events)

    _, old, new = compare(build, count=True)
    assert new == old and new["hits"] == 0


@pytest.mark.parametrize("failure", (("instruction_metadata_by_id", IndexError),
                                     ("terminator_use_id", IndexError),
                                     ("terminator_use_id", ValueError),
                                     ("phi_record", IndexError),
                                     ("terminator_case", IndexError),
                                     ("inline_error_edge_target", ValueError)))
def test_accessor_errors_and_swallowed_terminator_indexerror(failure):
    blocks = case([[1, 1, 1], []], {0: [0, 1]}, phis={1: [(2, [(0, 0)])]}, errors={0: [1]})
    result, _, _ = compare(lambda: FakeKernel(blocks, fail=failure))
    assert (result[0] == "value") == (failure == ("terminator_use_id", IndexError))


@pytest.mark.parametrize("shape", ("high-reuse", "multiple-seeds", "unique-seeds", "full-cache",
                                    "graph-only-high", "duplicate-seed"))
def test_counted_walk_and_cache_bound(shape):
    if shape == "high-reuse":
        blocks = case([[1], [2], []], {2: list(range(10))})
    elif shape == "multiple-seeds":
        blocks = case([[1], [2], []], {1: list(range(10)), 2: list(range(10))})
    elif shape == "unique-seeds":
        blocks = case([[1], [2], [3], []], {index: [index] for index in range(4)})
    elif shape == "full-cache":
        blocks = case([[1], [2], [3], []], {0: [0], 1: [1], 2: [2], 3: [3, 20, 21, 22]},
                      ((20, 0), (21, 0)))
    elif shape == "graph-only-high":
        blocks = case([[]], {0: [0, 1, 2]}, terms={0: [0]})
    else:
        blocks = case([[1], []], {1: [0, 1]}, ((0, 0), (1, 0)),
                      phis={1: [(2, [(0, 0), (1, 0), (0, 1), (1, 1)])]})
    result, old, new = compare(lambda: FakeKernel(blocks), count=True)
    assert result[0] == "value"
    assert new["writes"] <= len(blocks)
    assert new["pops"] <= old["pops"] and new["edges"] <= old["edges"]
    if shape in ("multiple-seeds", "unique-seeds"):
        assert new["hits"] == 0
        assert (new["pops"], new["edges"]) == (old["pops"], old["edges"])
    else:
        assert new["hits"] > 0 and new["pops"] < old["pops"]
    if shape == "high-reuse":
        assert old["pops"] == 30 and old["edges"] == 20
        assert new == dict(pops=3, edges=2, hits=9, writes=1)
    if shape == "full-cache":
        # Four keys fill the memo; two new-home misses cannot evict them.
        # The later undefined value 22 must still hit the existing (-1, 3).
        assert old["pops"] == 20 and old["edges"] == 15
        assert new == dict(pops=16, edges=12, hits=1, writes=len(blocks))
    if shape == "graph-only-high":
        assert result[1][1] == ((0, 3), (1, 1), (2, 2))
