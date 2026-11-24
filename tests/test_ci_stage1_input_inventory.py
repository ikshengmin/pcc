"""The Stage1 producer must be able to read every declared validation input."""

from scripts import bootstrap_platform, ci_worker_size_prior_gate


def test_stage1_hashes_the_complete_live_validation_input_inventory(monkeypatch):
    # The full compiler checkout identity is verified separately. Preserve the
    # real Stage1 code that opens and hashes every named validation input.
    monkeypatch.setattr(bootstrap_platform, "source_identity", lambda: {})
    gate = ci_worker_size_prior_gate
    inputs = gate.source_identity()["test_inputs"]
    assert set(gate.HOST_FILES) <= inputs.keys()
    assert inputs[gate.STAGE1_INPUT_INVENTORY_TEST] == gate.sha(__file__)
