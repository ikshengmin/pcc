"""CI-only source-native transport contracts; no native builds in this module."""
from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from scripts import ci_validation_native_inputs as native
from scripts import ci_validation_plan as planner
from tests.test_ci_stage1_bundle import COMMIT, LEASE, SOURCE, product


def environment():
    return {'GITHUB_REPOSITORY': LEASE['repository'],
            'GITHUB_WORKFLOW_REF': LEASE['repository'] + '/' + LEASE['workflow'] + '@refs/heads/master',
            'GITHUB_RUN_ID': str(LEASE['run_id']), 'GITHUB_RUN_ATTEMPT': str(LEASE['run_attempt']),
            'GITHUB_SHA': COMMIT}


def prepare(product, monkeypatch):
    directory, root, manifest, originals = product()
    checked = []
    monkeypatch.setattr(native.bundle, 'host_platform', lambda: 'linux-x86_64')
    monkeypatch.setattr(native, 'source_identity', lambda: copy.deepcopy(manifest['source']))
    def verify(archive):
        checked.append(archive)
        return archive, {'target_triple': 'x86_64-unknown-linux-gnu'}
    monkeypatch.setattr(native, 'verify_runtime', verify)
    return directory, root, manifest, originals, checked


@pytest.mark.parametrize('domain,lane', [('shared', lane) for lane in planner.LANES] + [
    ('cpp', 'integration'), ('ci-contracts', 'integration')])
def test_native_source_adopts_exact_existing_stage1_and_runtime_without_global_routing(product, monkeypatch, domain, lane):
    directory, root, manifest, originals, checked = prepare(product, monkeypatch)
    state, updates = native.admit(root, directory, domain=domain, lane=lane, environ=environment())
    runtime = root / manifest['paths']['runtime']
    assert checked == [runtime]
    assert updates['PCC_RUNTIME_ARCHIVE'] == str(runtime)
    assert updates['PCC_THREADED_RUNTIME_ARCHIVE'] == str(runtime)
    assert updates['PCC_CURRENT_PCC1'] == str(root / manifest['paths']['compiler'])
    assert updates['PCC_PLATFORM_TEST_COMPILER'] == updates['PCC_CURRENT_PCC1']
    assert updates['PCC_REQUIRE_CURRENT_PCC1'] == '1'
    assert updates['PCC_TEST_NO_NATIVE_PROVISIONING'] == updates['PCC_NO_AUTO_PCC1'] == '1'
    assert 'PCC_TEST_COMPILER' not in updates
    assert updates['PCC_CI_STAGE1_MANIFEST_SHA256'] == native.digest(directory / 'manifest.json')
    assert all((root / name).read_bytes() == data for name, data in originals.items())
    assert native.verify_after(root, state) == state
    assert checked == [runtime, runtime]


@pytest.mark.parametrize('domain,lane,directory', [
    ('shared', 'ordinary', None), ('cpp', 'integration', None),
    ('ci-contracts', 'integration', None),
    ('ci-contracts', 'ordinary', Path('.')), ('ci-contracts', 'reference', Path('.')),
    ('cpp', 'ordinary', Path('.')), ('cpp', 'reference', Path('.')),
    ('rust', 'integration', Path('.')), ('shared', 'unknown', Path('.')),
])
def test_missing_or_wrong_lane_never_admits_or_provisions(domain, lane, directory, tmp_path, monkeypatch):
    monkeypatch.setattr(native.bundle, 'import_bundle', lambda *a: pytest.fail('unexpected admission'))
    with pytest.raises(ValueError, match='native-dependent source validation requires'):
        native.admit(tmp_path, directory, domain=domain, lane=lane, environ={})


@pytest.mark.parametrize('name', [
    'PCC_CURRENT_PCC1', 'PCC_TEST_COMPILER', 'PCC_PLATFORM_TEST_COMPILER', 'PCC_RUNTIME_ARCHIVE',
    'PCC_THREADED_RUNTIME_ARCHIVE', 'PCC_CI_STAGE1_MANIFEST', 'PCC_CI_STAGE1_MANIFEST_SHA256',
])
@pytest.mark.parametrize('domain,lane', [('shared', 'ordinary'), ('ci-contracts', 'integration')])
def test_inherited_native_inputs_cannot_replace_this_run_bundle(name, tmp_path, monkeypatch, domain, lane):
    monkeypatch.setattr(native.bundle, 'host_platform', lambda: 'linux-x86_64')
    monkeypatch.setattr(native.bundle, 'import_bundle', lambda *a: pytest.fail('override reached admission'))
    with pytest.raises(ValueError, match='inherited native source-test input overrides'):
        native.admit(tmp_path, tmp_path, domain=domain, lane=lane, environ={name: 'unverified'})


@pytest.mark.parametrize('field,value', [
    ('GITHUB_SHA', 'c' * 40), ('GITHUB_RUN_ID', '999'), ('GITHUB_RUN_ATTEMPT', '99'),
    ('GITHUB_REPOSITORY', 'other/pcc'),
])
@pytest.mark.parametrize('domain,lane', [('shared', 'ordinary'), ('ci-contracts', 'integration')])
def test_bundle_from_another_source_run_attempt_or_repository_is_rejected(field, value, product, monkeypatch, domain, lane):
    directory, root, _manifest, _originals, checked = prepare(product, monkeypatch)
    env = dict(environment(), **{field: value})
    with pytest.raises(ValueError):
        native.admit(root, directory, domain=domain, lane=lane, environ=env)
    assert checked == []


@pytest.mark.parametrize('domain,lane', [('shared', 'ordinary'), ('ci-contracts', 'integration')])
def test_runtime_provenance_failure_cannot_publish_fixture_environment(product, monkeypatch, domain, lane):
    directory, root, _manifest, _originals, _checked = prepare(product, monkeypatch)
    def reject(_archive):
        raise ValueError('authored stale runtime provenance control')
    monkeypatch.setattr(native, 'verify_runtime', reject)
    with pytest.raises(ValueError, match='stale runtime provenance'):
        native.admit(root, directory, domain=domain, lane=lane, environ=environment())


@pytest.mark.parametrize('target', ['manifest', 'runtime', 'compiler', 'source'])
@pytest.mark.parametrize('domain,lane', [('shared', 'ordinary'), ('ci-contracts', 'integration')])
def test_consumed_native_inputs_cannot_change_during_source_campaign(target, product, monkeypatch, domain, lane):
    directory, root, manifest, _originals, _checked = prepare(product, monkeypatch)
    state, _updates = native.admit(root, directory, domain=domain, lane=lane, environ=environment())
    if target == 'manifest':
        path = directory / 'manifest.json'
        path.write_bytes(path.read_bytes() + b'\n')
    elif target == 'source':
        monkeypatch.setattr(native, 'source_identity', lambda: dict(manifest['source'], source_sha256='c' * 64))
    else:
        path = root / manifest['paths'][target]
        path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(ValueError):
        native.verify_after(root, state)


@pytest.mark.parametrize('field,value', [
    ('commit', 'c' * 40), ('clean_commit', False), ('source_sha256', 'c' * 64),
])
def test_ci_contract_native_inputs_require_matching_clean_source(field, value, product, monkeypatch):
    directory, root, manifest, _originals, checked = prepare(product, monkeypatch)
    monkeypatch.setattr(native, 'source_identity', lambda: dict(manifest['source'], **{field: value}))
    with pytest.raises(ValueError):
        native.admit(root, directory, domain='ci-contracts', lane='integration', environ=environment())
    assert checked == []


def test_native_runner_admits_before_collection_and_keeps_existing_resource_contracts():
    root = Path(__file__).resolve().parents[1]
    source = (root / 'scripts/ci_validation_run.py').read_text()
    assert source.index('ci_validation_native_inputs.admit(') < source.index('shards = collection_plan(files)')
    assert 'env.update(native_updates)' in source
    assert 'ci_validation_native_inputs.verify_after(ROOT, native_inputs)' in source
    assert 'TIMEOUT = 90' in source and 'RSS = 1073741824' in source
    assert 'PCC_TEST_NO_NATIVE_PROVISIONING="1"' in source
    assert 'ensure_target_runtime' not in source and 'prepare_runtime' not in source


@pytest.mark.parametrize('changes,scope', [
    ([("M", "pcc/frontends/cpp/parser.py")], 'auto'),
    ([("M", "pcc/frontends/rust/syntax.py")], 'frontends'),
    ([("M", "pcc/runtime/py/owner.py")], 'auto'),
    ([("M", ".github/workflows/pcc1-package-parity.yml")], 'auto'),
    ([("D", "tests/go/test_retired.py")], 'go'),
    ([("M", "docs/status.md")], 'auto'),
    ([], 'shared'),
    ([], 'all'),
])
def test_source_job_matrices_are_an_exact_disjoint_partition(changes, scope):
    result = planner.select(changes, scope=scope)
    complete = result['matrix']['include']
    independent = result['matrix_independent']['include']
    native_rows = result['matrix_native']['include']
    identity = lambda rows: [(row['domain'], row['lane']) for row in rows]
    assert len(identity(complete)) == len(set(identity(complete)))
    assert set(identity(independent)).isdisjoint(identity(native_rows))
    assert sorted(identity(independent) + identity(native_rows)) == sorted(identity(complete))
    required = {('shared', lane) for lane in planner.LANES} | {
        ('cpp', 'integration'), ('ci-contracts', 'integration')}
    assert independent == [row for row in complete if (row['domain'], row['lane']) not in required]
    assert native_rows == [row for row in complete if (row['domain'], row['lane']) in required]
    assert result['source_independent'] is bool(independent)
    assert result['source_native'] is bool(native_rows)
    assert result['source'] is bool(complete)
    assert set(identity(complete)) == {(domain, lane) for domain in result['domains'] for lane in planner.LANES}


@pytest.mark.parametrize('language', planner.LANGUAGES)
def test_frontend_only_native_requirements_do_not_enable_legacy_qualification(language):
    result = planner.select([('M', f'pcc/frontends/{language}/parser.py')])
    assert not result['legacy'] and result['source_independent']
    if language == 'cpp':
        assert result['source_native'] and result['native_stage1']
        assert result['matrix_native'] == {'include': [{'domain': 'cpp', 'lane': 'integration'}]}
        assert result['matrix_independent']['include'] == [
            {'domain': 'cpp', 'lane': lane} for lane in ('ordinary', 'reference')]
        assert result['matrix_native_stage1'] == {'include': [planner.NATIVE_PLATFORMS[0]]}
    else:
        assert not result['source_native'] and not result['native_stage1']
        assert result['matrix_native'] == result['matrix_native_stage1'] == {'include': []}
        assert result['matrix_independent'] == result['matrix']


def test_legacy_keeps_all_original_native_producer_platforms():
    result = planner.select([('M', 'pcc/runtime/py/owner.py')])
    assert result['legacy'] and result['native_stage1']
    assert result['matrix_native_stage1']['include'] == [
        {'platform': 'linux-x86_64', 'runner': 'ubuntu-24.04', 'docker_platform': 'linux/amd64'},
        {'platform': 'linux-aarch64', 'runner': 'ubuntu-24.04-arm', 'docker_platform': 'linux/arm64'},
        {'platform': 'windows-x86_64', 'runner': 'windows-2022'},
    ]


def test_workflow_consumes_same_run_attempt_bundle_only_in_native_job():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / '.github/workflows/pcc1-package-parity.yml').read_text()
    independent = workflow.split('\n  source-validation:\n', 1)[1].split('\n  shared-source-validation:\n', 1)[0]
    consumer = workflow.split('\n  shared-source-validation:\n', 1)[1].split('\n  numpy-source-frontier:\n', 1)[0]
    assert 'needs: validation-plan' in independent
    assert 'other-platform-native-wheel' not in independent
    assert 'actions/download-artifact' not in independent and '--stage1-bundle' not in independent
    assert "outputs.source_independent == 'true'" in independent
    assert 'matrix: ${{ fromJSON(needs.validation-plan.outputs.matrix_independent) }}' in independent
    assert 'needs: [validation-plan, other-platform-native-wheel]' in consumer
    assert "!cancelled() && needs.validation-plan.result == 'success'" in consumer
    assert "outputs.source_native == 'true'" in consumer
    assert 'matrix: ${{ fromJSON(needs.validation-plan.outputs.matrix_native) }}' in consumer
    assert 'actions/download-artifact@v7' in consumer
    assert 'name: stage1-native-linux-x86_64-${{ github.sha }}-${{ github.run_attempt }}' in consumer
    assert '--stage1-bundle "$RUNNER_TEMP/source-native-input"' in consumer
    assert consumer.index('actions/download-artifact@v7') < consumer.index('scripts/ci_validation_run.py')
    for job in (independent, consumer):
        assert 'PCC_TEST_NO_NATIVE_PROVISIONING: "1"' in job
        assert 'PCC_NO_AUTO_PCC1: "1"' in job
        assert 'timeout-minutes: 15' in job and 'continue-on-error' not in job
        assert job.count('scripts/ci_validation_run.py') == 1
        assert 'name: Source / ${{ matrix.domain }} / ${{ matrix.lane }}' in job
        assert 'name: source-${{ matrix.domain }}-${{ matrix.lane }}-${{ github.sha }}-${{ github.run_attempt }}' in job
        assert '${{ runner.temp }}/source-validation' in job
        assert '${{ runner.temp }}/reference-tools' in job
    assert 'matrix: ${{ steps.select.outputs.matrix }}' in workflow
    for output in ('source_independent', 'matrix_independent', 'source_native', 'matrix_native',
                   'native_stage1', 'matrix_native_stage1'):
        assert output + ': ${{ steps.select.outputs.' + output + ' }}' in workflow
    producer = workflow.split('\n  other-platform-native-wheel:\n', 1)[1].split('\n  pcc1-package-parity:\n', 1)[0]
    assert 'needs: validation-plan' in producer
    assert 'needs: [validation-plan, source-validation]' not in producer
    assert 'name: stage1-native-${{ matrix.platform }}-${{ github.sha }}-${{ github.run_attempt }}' in producer

    assert "outputs.native_stage1 == 'true' && (!inputs.full_chain || inputs.pytest_selection == 'not integration')" in producer
    assert 'matrix: ${{ fromJSON(needs.validation-plan.outputs.matrix_native_stage1) }}' in producer
    assert producer.count('scripts/ci_worker_size_prior_gate.py --out-dir build/worker-prior-gate --phase stage1') == 1
    later = producer.split('      # One integration budget,', 1)[1]
    for line in later.splitlines():
        if line.strip().startswith('if:') and line.strip() != 'if: always()':
            assert "needs.validation-plan.outputs.legacy == 'true'" in line


@pytest.mark.parametrize('path,native_required', [
    ('pcc/frontends/cpp/parser.py', True), ('pcc/frontends/rust/parser.py', False),
    ('pcc/runtime/py/owner.py', True), ('docs/status.md', False),
])
def test_producer_preserves_full_chain_exclusion_for_every_plan(path, native_required):
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/pcc1-package-parity.yml').read_text()
    producer = workflow.split('\n  other-platform-native-wheel:\n', 1)[1].split('\n  pcc1-package-parity:\n', 1)[0]
    expression = re.search(r"(?m)^    if: \$\{\{ (.+) \}\}$", producer).group(1)
    plan = planner.select([('M', path)])
    assert plan['native_stage1'] is native_required
    for full_chain in (False, True):
        for selection in ('both', 'integration', 'not integration'):
            rendered = expression.replace('needs.validation-plan.outputs.native_stage1',
                                          repr(str(plan['native_stage1']).lower()))
            rendered = rendered.replace('!inputs.full_chain', repr(not full_chain))
            rendered = rendered.replace('inputs.pytest_selection', repr(selection))
            rendered = rendered.replace('&&', ' and ').replace('||', ' or ')
            assert eval(rendered, {'__builtins__': {}}, {}) is (
                native_required and (not full_chain or selection == 'not integration'))


@pytest.mark.parametrize('language', planner.LANGUAGES)
@pytest.mark.parametrize('full_chain', ['false', 'true'])
@pytest.mark.parametrize('selection', ['both', 'integration', 'not integration'])
def test_dispatch_planner_validates_full_chain_before_any_job_output(
        tmp_path, monkeypatch, language, full_chain, selection):
    """Execute the real planner entry; Git/source discovery are inert controls."""
    import json
    event = tmp_path / 'event.json'
    event.write_text(json.dumps({'inputs': {'scope': language, 'full_chain': full_chain,
                                          'pytest_selection': selection}}))
    out, outputs = tmp_path / 'plan.json', tmp_path / 'outputs'
    monkeypatch.setattr('sys.argv', ['ci_validation_plan.py', '--out', str(out)])
    monkeypatch.setenv('GITHUB_EVENT_PATH', str(event))
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'workflow_dispatch')
    monkeypatch.setenv('GITHUB_SHA', COMMIT)
    monkeypatch.setenv('GITHUB_OUTPUT', str(outputs))
    monkeypatch.delenv('GITHUB_STEP_SUMMARY', raising=False)
    monkeypatch.setattr(planner, 'git', lambda *a: COMMIT)
    baseline_calls = []
    def baseline(after):
        baseline_calls.append(after)
        return {'head_sha': 'c' * 40}
    monkeypatch.setattr(planner, 'successful_validation_base', baseline)
    monkeypatch.setattr(planner, 'changed_paths', lambda *a: [('M', f'pcc/frontends/{language}/parser.py')])
    if full_chain == 'true' and selection != 'not integration':
        with pytest.raises(ValueError, match='full_chain=true requires pytest_selection'):
            planner.main()
        assert baseline_calls == [] and not out.exists() and not outputs.exists()
        return
    planner.main()
    result = json.loads(out.read_text())
    emitted = dict(line.split('=', 1) for line in outputs.read_text().splitlines())
    assert baseline_calls == [COMMIT]
    assert result['full_chain'] is (full_chain == 'true')
    assert json.loads(emitted['legacy']) is result['legacy']
    assert json.loads(emitted['matrix_native_stage1']) == result['matrix_native_stage1']
    if full_chain == 'true':
        assert result['legacy'] and result['native_stage1']
        assert result['domains'] == sorted((*planner.LANGUAGES, 'shared', 'ci-contracts'))
        assert result['matrix_native_stage1']['include'] == list(planner.NATIVE_PLATFORMS)
        assert result['matrix']['include'] == [
            {'domain': domain, 'lane': lane} for domain in result['domains'] for lane in planner.LANES]
    else:
        assert not result['legacy'] and result['domains'] == [language]
        expected = list(planner.NATIVE_PLATFORMS[:1]) if language == 'cpp' else []
        assert result['matrix_native_stage1']['include'] == expected


@pytest.mark.parametrize('value,expected', [(True, True), (False, False), ('true', True), ('false', False)])
def test_dispatch_accepts_boolean_and_event_string_forms(value, expected):
    assert planner.dispatch_full_chain({'full_chain': value, 'pytest_selection': 'not integration'}) is expected


@pytest.mark.parametrize('inputs', [
    {'full_chain': 'yes'}, {'full_chain': 1}, {'full_chain': None},
    {'pytest_selection': 'unknown'},
])
def test_dispatch_rejects_malformed_fixed_point_inputs(inputs):
    with pytest.raises(ValueError):
        planner.dispatch_full_chain(inputs)
