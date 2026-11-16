"""scripts/datasketches_bench.py: the cross-language DataSketches comparison.

Unit nodes check output validation, the summary (medians, pair wins) and that
an arm without a toolchain or owned frontend is reported UNRUN with its
reason. The gated nodes build the C++ and Rust reference arms from the pinned
sources and run the workload with a small N, requiring identical HLL and
Theta estimates across the two languages (the same hashing and work).
"""
import json
import shutil
import sys

import pytest

from scripts import datasketches_bench as bench


def rows(n, seconds):
    return '\n'.join(json.dumps({'family': family, 'n': n, 'seconds': seconds, 'result': 1.0})
                     for family in bench.FAMILIES)


def test_output_must_have_each_family_once_for_the_requested_n():
    assert list(bench.parse_output(rows(10, 0.5), 10)) == list(bench.FAMILIES)
    with pytest.raises(ValueError, match='unexpected workload output'):
        bench.parse_output(rows(11, 0.5), 10)
    with pytest.raises(ValueError, match='families differ'):
        bench.parse_output(rows(10, 0.5).rsplit('\n', 1)[0], 10)


def test_summary_reports_medians_and_pair_wins_against_the_first_arm():
    rounds = []
    for a, b in ((1.0, 2.0), (3.0, 1.0), (2.0, 1.5)):
        rounds.append({'a': {'rows': bench.parse_output(rows(4, a), 4)},
                       'b': {'rows': bench.parse_output(rows(4, b), 4)}})
    summary = bench.summarize(rounds, ['a', 'b'])
    assert summary['a']['hll8_lg12']['median_ns_per_update'] == pytest.approx(2.0e9 / 4)
    assert summary['b']['hll8_lg12']['median_ns_per_update'] == pytest.approx(1.5e9 / 4)
    assert summary['b']['hll8_lg12']['wins_against_a'] == 2 and 'wins_against_a' not in summary['a']['hll8_lg12']


@pytest.mark.parametrize('arm', sorted(bench.PCC_FRONTIERS) + ['java-ref', 'python-ref'])
def test_unavailable_arms_are_unrun_with_their_reason(arm):
    with pytest.raises(bench.Unrun) as caught:
        bench.unavailable_reference(arm)
    assert str(caught.value)


def test_main_records_unrun_arms_without_timing_them(tmp_path, capsys):
    assert bench.main(['--out', str(tmp_path / 'out'), '--n', '10', '--rounds', '1',
                       '--arms', 'java-pcc,python-ref']) == 0
    result = json.loads((tmp_path / 'out' / 'results.json').read_text())
    assert result['summary'] == {} and result['rounds'] == [{}] and set(result['unrun']) == {'java-pcc', 'python-ref'}
    assert result['screen_only'] is True and 'UNRUN' in capsys.readouterr().out


REFERENCES = None if shutil.which('rustc') and (shutil.which('xcrun') or shutil.which('clang++')) else \
    'needs rustc and a C++ compiler for the reference arms'


@pytest.mark.xdist_group(name='datasketches-reference')
@pytest.mark.pcc_gate(unavailable=REFERENCES)
def test_cpp_and_rust_reference_arms_do_equal_work(tmp_path):
    out = tmp_path / 'out'
    assert bench.main(['--out', str(out), '--n', '20000', '--rounds', '2', '--arms', 'cpp-ref,rust-ref']) == 0
    result = json.loads((out / 'results.json').read_text())
    assert set(result['summary']) == {'cpp-ref', 'rust-ref'} and len(result['rounds']) == 2
    for family in ('hll8_lg12', 'theta_lg12'):
        assert result['summary']['cpp-ref'][family]['result'] == result['summary']['rust-ref'][family]['result']
    for family in bench.FAMILIES:
        assert result['summary']['rust-ref'][family]['wins_against_cpp-ref'] in (0, 1, 2)
    assert sys.platform != 'darwin' or result['identity']['arms']['cpp-ref']['flags'] == '-std=c++17 -O2'
