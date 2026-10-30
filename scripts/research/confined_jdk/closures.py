"""Scope-local action-key memoization experiment, never installed in PCC."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json


@dataclass
class Counts:
    module_maps: int = 0
    summary_digests: int = 0
    summary_serialized_bytes: int = 0
    closure_walks: int = 0
    dependency_visits: int = 0
    edges: int = 0


def summary_size(summary):
    return len(json.dumps(summary.payload(), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True).encode("utf-8"))


class ActionKeyScope:
    """A fresh instance for each immutable GraphState, never a global cache.

    This uses #31755's shared-work principle, not its automatic-module-specific
    all-to-all shortcut. PCC dependency closures are not presumed all-to-all.
    Complete sorted (name, summary digest) facts remain in every action key.
    """

    def __init__(self, module, state, counts=None, *, eager=True):
        self.module = module
        self.state = state
        self.modules = state.module_map()
        self.digests = ({name: item.summary.digest() for name, item in self.modules.items()}
                        if eager else {})
        self.closures = {}
        self.counts = counts
        if counts is not None:
            counts.module_maps += 1
            counts.summary_digests += len(self.digests)
            if eager:
                counts.summary_serialized_bytes += sum(summary_size(item.summary) for item in self.modules.values())

    def digest(self, name):
        if name not in self.digests:
            self.digests[name] = self.modules[name].summary.digest()
            if self.counts is not None:
                self.counts.summary_digests += 1
                self.counts.summary_serialized_bytes += summary_size(self.modules[name].summary)
        return self.digests[name]

    def facts(self, name):
        if name in self.closures:
            return self.closures[name]
        facts = []
        seen = set()
        pending = list(reversed(self.modules[name].dependencies))
        if self.counts is not None:
            self.counts.closure_walks += 1
        while pending:
            dependency = pending.pop()
            if dependency in seen:
                continue
            seen.add(dependency)
            item = self.modules[dependency]
            facts.append((dependency, self.digest(dependency)))
            if self.counts is not None:
                self.counts.dependency_visits += 1
                self.counts.edges += len(item.dependencies)
            for child in reversed(item.dependencies):
                if child not in seen:
                    pending.append(child)
        result = sorted(facts)
        self.closures[name] = result
        return result

    def key(self, name, stage):
        module = self.module
        if stage not in module.ACTION_STAGES:
            raise ValueError("unknown action stage " + stage)
        item = self.modules.get(name)
        if item is None:
            raise KeyError(name)
        state = self.state
        return module._sha256_json({
            "compiler_digest": state.compiler_digest,
            "dependency_summaries": self.facts(name),
            "module": item.name,
            "options_digest": state.options_digest,
            "runtime_abi_digest": state.runtime_abi_digest,
            "schema": module.SCHEMA,
            "source_digest": item.source_digest,
            "stage": stage,
            "summary_digest": self.digest(name),
            "target": state.target,
        })


@contextmanager
def count_baseline(module, counts):
    """Diagnostic-only wrappers; never used during the timing observations."""
    original_map = module.GraphState.module_map
    original_digest = module.PublicSummary.digest
    original_closure = module._dependency_summary_closure

    class CountedMap(dict):
        def __getitem__(self, name):
            item = super().__getitem__(name)
            counts.dependency_visits += 1
            counts.edges += len(item.dependencies)
            return item

    def module_map(state):
        counts.module_maps += 1
        return CountedMap(original_map(state))

    def digest(summary):
        counts.summary_digests += 1
        counts.summary_serialized_bytes += summary_size(summary)
        return original_digest(summary)

    def closure(modules, item):
        counts.closure_walks += 1
        return original_closure(modules, item)

    module.GraphState.module_map = module_map
    module.PublicSummary.digest = digest
    module._dependency_summary_closure = closure
    try:
        yield
    finally:
        module.GraphState.module_map = original_map
        module.PublicSummary.digest = original_digest
        module._dependency_summary_closure = original_closure
