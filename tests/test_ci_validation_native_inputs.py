"""CI-only source-native transport contracts; no native builds in this module."""
from __future__ import annotations

import copy
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


def test_shared_source_adopts_exact_existing_stage1_and_runtime_without_global_routing(product, monkeypatch):
    directory, root, manifest, originals, checked = prepare(product, monkeypatch)
    state, updates = native.admit(root, directory, domain='shared', environ=environment())
    runtime = root / manifest['paths']['runtime']
    assert checked == [runtime]
    assert updates['PCC_RUNTIME_ARCHIVE'] == str(runtime)
    assert updates['PCC_THREADED_RUNTIME_ARCHIVE'] == str(runtime)
    assert updates['PCC_CURRENT_PCC1'] == str(root / manifest['paths']['compiler'])
    assert updates['PCC_REQUIRE_CURRENT_PCC1'] == '1'
    assert updates['PCC_TEST_NO_NATIVE_PROVISIONING'] == updates['PCC_NO_AUTO_PCC1'] == '1'
    assert 'PCC_TEST_COMPILER' not in updates
    assert updates['PCC_CI_STAGE1_MANIFEST_SHA256'] == native.digest(directory / 'manifest.json')
    assert all((root / name).read_bytes() == data for name, data in originals.items())
    assert native.verify_after(root, state) == state
    assert checked == [runtime, runtime]


@pytest.mark.parametrize('domain,directory', [('shared', None), ('cpp', None), ('rust', Path('.'))])
def test_missing_or_wrong_domain_never_admits_or_provisions(domain, directory, tmp_path, monkeypatch):
    monkeypatch.setattr(native.bundle, 'import_bundle', lambda *a: pytest.fail('unexpected admission'))
    with pytest.raises(ValueError, match='shared source validation requires'):
        native.admit(tmp_path, directory, domain=domain, environ={})


@pytest.mark.parametrize('name', [
    'PCC_CURRENT_PCC1', 'PCC_TEST_COMPILER', 'PCC_RUNTIME_ARCHIVE',
    'PCC_THREADED_RUNTIME_ARCHIVE', 'PCC_CI_STAGE1_MANIFEST', 'PCC_CI_STAGE1_MANIFEST_SHA256',
])
def test_inherited_native_inputs_cannot_replace_this_run_bundle(name, tmp_path, monkeypatch):
    monkeypatch.setattr(native.bundle, 'host_platform', lambda: 'linux-x86_64')
    monkeypatch.setattr(native.bundle, 'import_bundle', lambda *a: pytest.fail('override reached admission'))
    with pytest.raises(ValueError, match='inherited native source-test input overrides'):
        native.admit(tmp_path, tmp_path, domain='shared', environ={name: 'unverified'})


@pytest.mark.parametrize('field,value', [
    ('GITHUB_SHA', 'c' * 40), ('GITHUB_RUN_ID', '999'), ('GITHUB_RUN_ATTEMPT', '99'),
    ('GITHUB_REPOSITORY', 'other/pcc'),
])
def test_bundle_from_another_source_run_attempt_or_repository_is_rejected(field, value, product, monkeypatch):
    directory, root, _manifest, _originals, checked = prepare(product, monkeypatch)
    env = dict(environment(), **{field: value})
    with pytest.raises(ValueError):
        native.admit(root, directory, domain='shared', environ=env)
    assert checked == []


def test_runtime_provenance_failure_cannot_publish_fixture_environment(product, monkeypatch):
    directory, root, _manifest, _originals, _checked = prepare(product, monkeypatch)
    def reject(_archive):
        raise ValueError('authored stale runtime provenance control')
    monkeypatch.setattr(native, 'verify_runtime', reject)
    with pytest.raises(ValueError, match='stale runtime provenance'):
        native.admit(root, directory, domain='shared', environ=environment())


@pytest.mark.parametrize('target', ['manifest', 'runtime', 'compiler', 'source'])
def test_consumed_native_inputs_cannot_change_during_source_campaign(target, product, monkeypatch):
    directory, root, manifest, _originals, _checked = prepare(product, monkeypatch)
    state, _updates = native.admit(root, directory, domain='shared', environ=environment())
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


def test_shared_runner_admits_before_collection_and_keeps_existing_resource_contracts():
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
    shared = result['matrix_shared']['include']
    identity = lambda rows: [(row['domain'], row['lane']) for row in rows]
    assert len(identity(complete)) == len(set(identity(complete)))
    assert set(identity(independent)).isdisjoint(identity(shared))
    assert sorted(identity(independent) + identity(shared)) == sorted(identity(complete))
    assert independent == [row for row in complete if row['domain'] != 'shared']
    assert shared == [row for row in complete if row['domain'] == 'shared']
    assert result['source_independent'] is bool(independent)
    assert result['source_shared'] is bool(shared)
    assert result['source'] is bool(complete)
    assert set(identity(complete)) == {(domain, lane) for domain in result['domains'] for lane in planner.LANES}


def test_frontend_only_jobs_have_no_native_producer_dependency_or_empty_matrix():
    result = planner.select([('M', 'pcc/frontends/cpp/parser.py')])
    assert result['source_independent'] and not result['source_shared']
    assert result['matrix_shared'] == {'include': []}
    assert result['matrix_independent'] == result['matrix']


def test_workflow_consumes_same_run_attempt_bundle_only_in_shared_job():
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
    assert "outputs.source_shared == 'true'" in consumer
    assert 'matrix: ${{ fromJSON(needs.validation-plan.outputs.matrix_shared) }}' in consumer
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
    for output in ('source_independent', 'matrix_independent', 'source_shared', 'matrix_shared'):
        assert output + ': ${{ steps.select.outputs.' + output + ' }}' in workflow
    producer = workflow.split('\n  other-platform-native-wheel:\n', 1)[1].split('\n  pcc1-package-parity:\n', 1)[0]
    assert 'needs: validation-plan' in producer
    assert 'needs: [validation-plan, source-validation]' not in producer
    assert 'name: stage1-native-${{ matrix.platform }}-${{ github.sha }}-${{ github.run_attempt }}' in producer
