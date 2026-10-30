"""Use the real PCC frontend and owned backend verifier, never invented IR."""

import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import time

from .source_identity import observe_source


class PCCAdapter:
    def __init__(self, source_root=None, source_manifest=None):
        if source_root is None:
            source_root = os.environ.get("PCC_RESEARCH_SOURCE")
        if source_root is None:
            specification = importlib.util.find_spec("pcc")
            if specification is None or not specification.origin:
                raise ValueError("provide an actual PCC source root")
            source_root = Path(specification.origin).resolve().parent.parent
        self.source_root = Path(source_root).resolve()
        self.source_manifest = source_manifest
        self.source_identity = observe_source(self.source_root, source_manifest)
        sys.path.insert(0, str(self.source_root))
        from pcc.frontends.python.py_lift import parse_and_lift
        from pcc.frontends.python.type_infer import infer_module
        from pcc.frontends.python.codegen.layer1 import L1CodeGen
        from pcc.backend.self_backend_parse import parse_self_backend_module
        from pcc.backend.self_backend_verify import verify_parsed_module
        self.parse = parse_and_lift
        self.infer = infer_module
        self.codegen = L1CodeGen
        self.parse_ir = parse_self_backend_module
        self.verify = verify_parsed_module
        self.verify_source_identity()

    def verify_source_identity(self):
        current = observe_source(self.source_root, self.source_manifest)
        if current != self.source_identity:
            raise ValueError("PCC source changed during the experiment")
        return current

    def lower(self, source: str):
        timings = {}
        stage = "parse"
        started = time.perf_counter_ns()
        try:
            ast = self.parse(source, filename="ttf_input.py", module_name="ttf_input")
            timings[stage] = time.perf_counter_ns() - started
            stage, started = "infer", time.perf_counter_ns()
            typed = self.infer(ast)
            timings[stage] = time.perf_counter_ns() - started
            stage, started = "lower_and_serialize", time.perf_counter_ns()
            codegen = self.codegen(typed, ir_scaffold_mode="on")
            module = codegen.generate(typed)
            timings[stage] = time.perf_counter_ns() - started
            text = str(module)
            stage, started = "parse_ir", time.perf_counter_ns()
            parsed = self.parse_ir(text)
            timings[stage] = time.perf_counter_ns() - started
            stage, started = "verify_ir", time.perf_counter_ns()
            self.verify(parsed)
            timings[stage] = time.perf_counter_ns() - started
        except Exception as error:
            timings[stage] = time.perf_counter_ns() - started
            return {"accepted": False, "stage": stage,
                    "error_type": type(error).__name__, "error": str(error),
                    "timings_ns": timings}, None
        return {"accepted": True, "timings_ns": timings,
                "ir_bytes": len(text.encode()), "ir_lines": len(text.splitlines()),
                "ir_sha256": hashlib.sha256(text.encode()).hexdigest()}, text
