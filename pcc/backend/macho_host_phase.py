"""Opt-in bounded events for the existing host Mach-O link owner.

The process-tree supervisor already preserves stderr. No Python profiling
hook, frame retention, object snapshot or additional RSS sampler is needed.
"""

import json
import os
import sys
import time


def phase_callback_from_environment():
    try:
        if (sys.implementation.name != "cpython"
                or os.environ.get("PCC_MACHO_LINK_PHASE_EVENTS", "") != "1"):
            return None
        pid = os.getpid()
        sequence = 0
        failed = False
    except BaseException:
        return None

    def record(event):
        nonlocal sequence, failed
        if failed:
            return
        try:
            row = {
                "schema": "pcc.macho-link-phase.v1",
                "pid": pid,
                "sequence": sequence,
                "monotonic_ns": time.monotonic_ns(),
                "event": event,
            }
            sys.stderr.write("PCC_MACHO_LINK_PHASE " + json.dumps(row, separators=(",", ":")) + "\n")
            sys.stderr.flush()
            sequence += 1
        except BaseException:
            # Observation cannot replace a compilation/cleanup exception.
            # Disable this sink without keeping an exception or traceback.
            failed = True

    return record


def emit_phase(callback, event):
    if callback is not None:
        try:
            callback(event)
        except BaseException:
            # Private sinks share the stderr sink's non-interference rule.
            pass
