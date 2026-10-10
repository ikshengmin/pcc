"""Bounded diagnostics for the isolated frontend worker's indexed path.

Enabled only by the caller's existing worker-timing policy.  The emitter never
reads an environment variable or retains an IR/function object for these totals.
Indexes are stable, with two explicitly inclusive parents (2 and 6).
"""

import time


_PHASE_NAMES = (
    "frontend_generate_ns",          # 0
    "frontend_release_pre_ns",       # 1
    "backend_call_inclusive_ns",     # 2: contains 3..7 and 12
    "prepare_verify_ns",             # 3
    "prepare_layout_ns",             # 4
    "stackmap_plan_ns",              # 5
    "function_emit_inclusive_ns",    # 6: contains 12
    "backend_finalize_ns",           # 7
    "frontend_release_post_ns",      # 8
    "object_assemble_ns",            # 9
    "object_encode_ns",              # 10
    "object_write_ns",               # 11
    "function_setup_ns",             # 12: child of 6
)


class ModulePhaseTiming:
    """Thirteen scalar aggregates, one best-effort report per module attempt."""

    def __init__(self, module_name: str):
        self.module_name = module_name
        self.target = "unselected"
        self.route = "not-direct"
        self.totals_ns = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        self.failed = False
        self.reported = False

    def start(self) -> int:
        if self.failed:
            return 0
        try:
            started_ns = time.perf_counter_ns()
            # Owned native clock failure uses zero rather than raising.
            if started_ns <= 0:
                self.failed = True
                return 0
            return started_ns
        except Exception:
            self.failed = True
            return 0

    def add(self, phase: int, started_ns: int) -> None:
        if self.failed or started_ns == 0:
            return
        try:
            elapsed_ns = time.perf_counter_ns() - started_ns
            if elapsed_ns < 0 or phase < 0 or phase >= 13:
                self.failed = True
                return
            self.totals_ns[phase] += elapsed_ns
        except Exception:
            self.failed = True

    def report(self, stream, complete: bool) -> None:
        # Reporting happens after the worker's existing codegen timer stops,
        # including on its normal exception path.  A killed process may emit no
        # summary; incomplete scopes are deliberately not invented or estimated.
        if self.reported:
            return
        self.reported = True
        try:
            parts = [
                "pcc indexed phase totals",
                "module=" + self.module_name,
                "target=" + self.target,
                "route=" + self.route,
                "codegen_complete=" + str(int(complete)),
                "clock_ok=" + str(int(not self.failed)),
            ]
            index = 0
            while index < 13:
                parts.append(_PHASE_NAMES[index] + "=" + str(self.totals_ns[index]))
                index += 1
            stream.write(" ".join(parts) + "\n")
        except Exception:
            # Optional diagnostics must not replace a compiler result/error.
            pass
