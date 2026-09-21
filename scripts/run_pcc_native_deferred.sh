#!/usr/bin/env bash
# Release each compiler process's heap before the next native phase.
set -euo pipefail
if [[ $# -lt 5 || "$4" != "--" ]]; then
    echo "usage: run_pcc_native_deferred.sh COMPILER CODEGEN_PLAN LINK_PLAN -- COMMAND..." >&2
    exit 2
fi
compiler="$1"
codegen_plan="$2"
link_plan="$3"
shift 4
if ! "${compiler}" --pcc-native-deferred-worker --check; then
    echo "native deferred execution is unavailable in ${compiler}; rebuild stage1 from current sources (host Python fallback is forbidden)" >&2
    exit 2
fi
"$@"
if [[ -n "${codegen_plan}" && -f "${codegen_plan}" ]]; then
    exec "${compiler}" --pcc-native-deferred-worker "${codegen_plan}"
elif [[ -n "${link_plan}" && -f "${link_plan}" ]]; then
    exec "${compiler}" --pcc-native-deferred-worker "${link_plan}"
fi
