#!/bin/sh
# Linux-only host correctness launcher. No compiler/runtime build or benchmark.
# Requires existing pytest/xdist interpreter, absolute shared lock, and test paths.
set -eu
: "${PCC_RESEARCH_PYTHON:?set an existing pytest/xdist interpreter path}"
: "${PCC_RESEARCH_LOCK:?set the shared absolute research lock path}"
case "$PCC_RESEARCH_PYTHON:$PCC_RESEARCH_LOCK" in
    /*:/*) ;;
    *) echo 'interpreter and shared lock must be absolute paths' >&2; exit 2 ;;
esac
if [ "$#" -eq 0 ]; then
    echo 'supply explicit host-only test paths; no broad default selection' >&2
    exit 2
fi
# This per-child setting reduces glibc arena reservation, not the memory cap.
# Preserve repository pytest/conftest defaults, including six xdist workers.
exec flock -n "$PCC_RESEARCH_LOCK" timeout --signal=TERM --kill-after=5s 60s \
    env MALLOC_ARENA_MAX=2 UV_PYTHON_DOWNLOADS=never \
    uv run --offline --no-project --python "$PCC_RESEARCH_PYTHON" \
    "$PCC_RESEARCH_PYTHON" -c '
import resource
import runpy
import sys
if sys.platform != "linux":
    raise SystemExit("bounded host validation requires Linux resource limits")
soft, hard = resource.getrlimit(resource.RLIMIT_AS)
limits = [512 * 1024 * 1024]
limits.extend(value for value in (soft, hard) if value != resource.RLIM_INFINITY)
resource.setrlimit(resource.RLIMIT_AS, (min(limits), hard))
sys.argv = ["pytest", "-x", *sys.argv[1:]]
runpy.run_module("pytest", run_name="__main__")
' "$@"
