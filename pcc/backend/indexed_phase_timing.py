"""Bounded diagnostics for the isolated frontend worker's indexed path.

Enabled only by the caller's existing worker-timing policy.  The emitter never
reads an environment variable or retains an IR/function object for these totals.
Indexes are stable; assembly children 13..15 belong to inclusive parent 9.
"""

import sys
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
    "assembly_parse_ns",             # 13: child of 9
    "assembly_measure_ns",           # 14: child of 9
    "assembly_emit_finalize_ns",     # 15: child of 9, not pure encoding
)


class ModulePhaseTiming:
    """Sixteen scalar aggregates and three coarse assembly boundary pairs."""

    def __init__(self, module_name: str):
        self.module_name = module_name
        self.target = "unselected"
        self.route = "not-direct"
        self.totals_ns = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
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
        except BaseException:
            self.failed = True
            return 0

    def add(self, phase: int, started_ns: int) -> None:
        if self.failed or started_ns == 0:
            return
        try:
            elapsed_ns = time.perf_counter_ns() - started_ns
            if elapsed_ns < 0 or phase < 0 or phase >= 16:
                self.failed = True
                return
            self.totals_ns[phase] += elapsed_ns
        except BaseException:
            self.failed = True

    def begin_assembly(self, phase: int) -> int:
        # These three boundaries run once each, outside all instruction loops.
        try:
            if phase < 13 or phase > 15:
                return 0
            started_ns = self.start()
            _write_event(
                "assembly", _PHASE_NAMES[phase][:-3], "object_assemble",
                "begin", not self.failed, 0,
                _bounded_module_name(self.module_name),
            )
            return started_ns
        except BaseException:
            return 0

    def complete_assembly(self, phase: int, started_ns: int) -> None:
        try:
            if phase < 13 or phase > 15:
                return
            before = self.totals_ns[phase]
            self.add(phase, started_ns)
            _write_event(
                "assembly", _PHASE_NAMES[phase][:-3], "object_assemble",
                "complete", not self.failed and started_ns > 0,
                self.totals_ns[phase] - before,
                _bounded_module_name(self.module_name),
            )
        except BaseException:
            pass

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
            while index < 16:
                parts.append(_PHASE_NAMES[index] + "=" + str(self.totals_ns[index]))
                index += 1
            stream.write(" ".join(parts) + "\n")
        except BaseException:
            # Optional diagnostics must not replace a compiler result/error.
            pass


# A fixed vocabulary keeps coordinator receipts bounded and prevents accidental
# source, command, path, or environment text from entering this diagnostic sink.
_COORDINATOR_PARENTS = {
    "result_read": "none",
    "runtime_verify": "none",
    "artifact_link": "none",
    "elf_stage_inputs": "artifact_link",
    "elf_link_image": "artifact_link",
    "elf_image_select_inputs": "elf_link_image",
    "elf_image_classify_layout": "elf_link_image",
    "elf_image_allocate_copy": "elf_link_image",
    "elf_image_resolve_relocate": "elf_link_image",
    "elf_image_finalize": "elf_link_image",
    "elf_verify_inputs": "artifact_link",
    "elf_write_image": "artifact_link",
    "executable_publish": "artifact_link",
    "artifact_cleanup": "none",
}


def _bounded_module_name(module_name: str) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_."
    return "".join(char if char in allowed else "_" for char in module_name[:160])


def _write_event(scope, phase, parent, event, clock_ok, duration_ns, module_name=""):
    try:
        parts = [
            "pcc coarse phase", "scope=" + scope, "phase=" + phase,
            "parent=" + parent, "event=" + event,
            "clock_ok=" + str(int(clock_ok)),
        ]
        if module_name:
            parts.append("module=" + module_name)
        # A begin receipt is intentionally incomplete. Neither an exception nor
        # watchdog termination fabricates a completion or elapsed duration.
        if event == "complete" and clock_ok:
            parts.append("duration_ns=" + str(duration_ns))
        sys.stderr.write(" ".join(parts) + "\n")
        sys.stderr.flush()
    except BaseException:
        # Even reporting failure during unwinding cannot replace the operation's
        # original exception (including KeyboardInterrupt/SystemExit).
        pass


class CoarsePhaseTiming:
    """At most fourteen fixed coordinator boundary pairs in the direct build path."""

    def __init__(self):
        self.failed = False

    def begin(self, phase: str) -> int:
        try:
            if phase not in _COORDINATOR_PARENTS:
                return 0
            started_ns = 0
            if not self.failed:
                try:
                    started_ns = time.perf_counter_ns()
                    if started_ns <= 0:
                        self.failed = True
                        started_ns = 0
                except BaseException:
                    self.failed = True
            _write_event(
                "coordinator", phase, _COORDINATOR_PARENTS[phase],
                "begin", not self.failed, 0,
            )
            return started_ns
        except BaseException:
            return 0

    def complete(self, phase: str, started_ns: int) -> None:
        try:
            if phase not in _COORDINATOR_PARENTS:
                return
            elapsed_ns = 0
            if not self.failed and started_ns > 0:
                try:
                    elapsed_ns = time.perf_counter_ns() - started_ns
                    if elapsed_ns < 0:
                        self.failed = True
                except BaseException:
                    self.failed = True
            _write_event(
                "coordinator", phase, _COORDINATOR_PARENTS[phase],
                "complete", not self.failed and started_ns > 0, elapsed_ns,
            )
        except BaseException:
            pass


    def elf_classification_skips(self, completed_sections: int, total_sections: int,
                                 skipped_sections: int, skipped_relocations: int) -> bool:
        """One fixed-field snapshot; caller coalesces at most eight quantiles.

        Sections are unweighted. Skipped relocation rows are a lower bound on
        avoided classification work, not total rows, elapsed time, or a speedup.
        """
        try:
            values = (completed_sections, total_sections, skipped_sections, skipped_relocations)
            if any(type(value) is not int or value < 0 for value in values):
                return False
            if not 0 < completed_sections <= total_sections or skipped_sections > completed_sections:
                return False
            parts = [
                "pcc elf classification skips",
                "completed_sections=" + str(completed_sections),
                "total_sections=" + str(total_sections),
                "skipped_sections=" + str(skipped_sections),
                "skipped_relocations_lower_bound=" + str(skipped_relocations),
            ]
            sys.stderr.write(" ".join(parts) + "\n")
            sys.stderr.flush()
            return True
        except BaseException:
            return False


    def elf_relocation_progress(self, event, completed_sections, total_sections,
                                completed_relocations, total_relocations) -> bool:
        """At most one scale, one GOT boundary and eight completed-row quantiles.

        R includes NONE rows. Section progress credits their initial resident
        row weights, not actual applied rows, resolution calls, throughput or time. Unknown/custom scale cannot publish a partial total.
        """
        try:
            if type(event) is not str or event not in ("scale", "scale_unknown", "got_complete", "sections"):
                return False
            limit = ((1 << 63) - 1) // 8
            values = (completed_sections, total_sections, completed_relocations, total_relocations)
            if any(type(value) is not int or value < 0 or value > limit for value in values):
                return False
            if event == "scale_unknown":
                if any(values):
                    return False
                parts = ["pcc elf relocation progress", "event=scale", "total_relocations_known=0"]
            else:
                if completed_sections > total_sections or completed_relocations > total_relocations:
                    return False
                if event == "sections":
                    if not completed_sections or not completed_relocations:
                        return False
                elif completed_sections or completed_relocations:
                    return False
                parts = ["pcc elf relocation progress", "event=" + event]
                if event == "scale":
                    parts.extend(("count_basis=initial_resident", "total_relocations_known=1",
                                  "total_relocations=" + str(total_relocations)))
                elif event == "sections":
                    parts.extend(("count_basis=initial_resident", "completed_sections=" + str(completed_sections),
                                  "total_sections=" + str(total_sections),
                                  "completed_section_initial_relocations=" + str(completed_relocations),
                                  "total_relocations=" + str(total_relocations)))
            sys.stderr.write(" ".join(parts) + "\n")
            sys.stderr.flush()
            return True
        except BaseException:
            return False
