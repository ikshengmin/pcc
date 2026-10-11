"""Exact real-host parser object controls for the existing CI invocation."""

from tests.python.test_x86_64_assembler_lifetime import (
    test_packed_instruction_spans_bound_storage_and_preserve_entry_order,
    test_packed_span_object_matches_unpacked_parser_on_same_input,
    test_packed_span_runs_retire_during_final_encoding,
    test_packed_span_corruption_is_rejected,
    test_packed_span_signed_width_preserves_boundary,
    test_label_dense_span_storage_never_inflates_host_representation,
)
from tests.python.test_x86_64_numeric_data_batching import (
    test_small_or_disabled_bounds_preserve_mixed_width_complete_object,
    test_symbol_label_alignment_zero_size_and_section_barriers,
    test_full_coff_bytes_keep_symbol_relocation_stackmap_and_unwind_sections,
)
