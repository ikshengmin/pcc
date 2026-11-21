"""Shared labels and slot numbers for the optional GC phase ledger."""

# Where the world is released for a callback, the release-to-reacquire
# interval is kept separate; it is not necessarily mutator stop time.
PHASES = (
    "runtime_other", "retired_sweep_drain", "mark_other", "registered_roots",
    "prepare_heap_for_mark", "capture_sort_root_words", "pin_owner_lookup",
    "sweep_other", "candidate_recheck", "weak_targets", "select_and_call_finalizers",
    "clear_reclaim", "world_released_until_reacquire", "gray_drain",
)
PHASE_COUNT = len(PHASES)
PUBLIC_STW_COUNTERS = (119, 120, 121)

# Report slots: raw header, per-phase aggregate, same-event maximum,
# and the number of elapsed windows below the clock's microsecond resolution.
WINDOWS, TOTAL_US, MAXIMUM_US, INVALID, AGGREGATE = range(5)
LONGEST = AGGREGATE + PHASE_COUNT
ZERO_US_WINDOWS = LONGEST + PHASE_COUNT
SLOTS = ZERO_US_WINDOWS + 1

# The owner keeps this context on its own stack. Use the same i64/pointer
# word stride for the supported 64-bit runtime targets and result array.
WORD_BYTES = 8
CURRENT_PHASE, LAST_US, PHASE_TOTALS = range(0, 3 * WORD_BYTES, WORD_BYTES)
SAVED_WINDOW_PHASE = PHASE_TOTALS + PHASE_COUNT * WORD_BYTES
PREVIOUS_OWNER = SAVED_WINDOW_PHASE + WORD_BYTES
CONTEXT_INVALID = PREVIOUS_OWNER + WORD_BYTES
CONTEXT_BYTES = CONTEXT_INVALID + WORD_BYTES
