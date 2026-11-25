"""Shared admission checks for single-collector native execution receipts."""

import json


def read_receipt(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            assert key not in result, "duplicate receipt key: " + key
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)


def verify_executions(record, binary, output):
    assert record["schema"] == "pcc.ownership-regression.v2"
    assert "gc_backends" not in record
    executions = record["executions"]
    assert len(executions) == 1
    row = executions[0]
    assert set(row) == {"command", "returncode", "stdout", "stderr",
                        "start_ns", "end_ns", "elapsed_ns"}
    assert type(row["returncode"]) is int
    assert row["command"] == [str(binary)]
    assert (row["returncode"], row["stdout"], row["stderr"]) == (0, output, "")
    assert all(type(row[k]) is int for k in ("start_ns", "end_ns", "elapsed_ns"))
    assert 0 <= row["start_ns"] <= row["end_ns"]
    assert row["elapsed_ns"] == row["end_ns"] - row["start_ns"]
    return executions


