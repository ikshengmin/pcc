"""Authored receipt models, not evidence of real pytest/compiler execution.

Only checkout identity/module discovery are modeled. Collection, ownership,
phase summaries, batching and the aggregate are the actual source functions.
"""
from __future__ import annotations

import copy
import base64
import hashlib
import json
from pathlib import Path

import pytest

from scripts import ci_validation_coverage as coverage
from scripts import ci_validation_owners as owners
from scripts import ci_validation_run as runner


def put(path, value):
    runner.save(path, value)


def events(path, value):
    path.write_text(''.join(json.dumps(row) + '\n' for row in value))


def mutate(path, change):
    value = json.loads(path.read_text())
    change(value)
    put(path, value)


def model(tmp_path, monkeypatch, *, owner_deselected=False):
    root = tmp_path / 'checkout'
    root.mkdir()
    (root / 'pyproject.toml').write_text('[tool.pcc-ci.group_policy]\n')
    module = 'tests/go/test_model.py'
    node = module + '::test_reference'
    manifest = {'schema': 1, 'default_platform': 'linux-x86_64', 'overrides': [
        {'domain': 'go', 'lane': 'reference', 'nodeid': node,
         'platforms': ['linux-x86_64', 'macos-arm64'],
         'tools': {'linux-x86_64': [], 'macos-arm64': ['clang']}}]}
    manifest['overrides'].append({'domain': 'go', 'lane': 'reference', 'nodeid': module + '::test_darwin',
        'platforms': ['macos-arm64'], 'tools': {'macos-arm64': ['clang']}})
    put(root / owners.MANIFEST, manifest)
    _, sha = owners.read_manifest(root)
    current = dict(repository='example/pcc', sha='a' * 40, run_id='17', run_attempt='2')
    environ = {'GITHUB_' + key.upper(): value for key, value in current.items()}
    source = dict(sha=current['sha'], files={'test.py': 'b' * 64}, sparse_omissions=[],
                  ownership_manifest=manifest, ownership_manifest_sha256=sha)
    monkeypatch.setattr(runner, 'identity', lambda: copy.deepcopy(source))
    monkeypatch.setattr(runner, 'modules', lambda domain, root=root: [module])
    matrix = [dict(domain='go', lane=lane) for lane in runner.LANES]
    extra = owners.additional_campaigns(['go'], manifest)
    plan = dict(schema=1, context=current, after=current['sha'], domains=['go'], source=True,
                ownership_manifest_sha256=sha, matrix={'include': matrix}, source_independent=True,
                matrix_independent={'include': matrix}, source_native=False, matrix_native={'include': []},
                source_platform=True, matrix_platform={'include': extra})
    plan_path = tmp_path / 'validation-plan.json'
    put(plan_path, plan)
    artifacts = tmp_path / 'artifacts'
    artifacts.mkdir()
    raw = [dict(file=module, function='test_' + lane, nodeid=module + '::test_' + lane,
                canonical_nodeid=module + '::test_' + lane, lane=lane, groups=[], gates=[],
                reference_activity='model', selected_by_repository=True) for lane in runner.LANES]
    raw.append(dict(raw[-1], function='test_darwin', nodeid=module + '::test_darwin',
                    canonical_nodeid=module + '::test_darwin'))
    base_rows = raw
    directories = {}
    for item in [dict(row, platform='linux-x86_64') for row in matrix] + extra:
        lane, platform = item['lane'], item['platform']
        raw = copy.deepcopy(base_rows)
        raw[-1]['gates'] = [{'unavailable': 'Darwin SDK only' if platform == 'linux-x86_64' else None}]
        raw[-1]['selected_by_repository'] = platform == 'macos-arm64'
        if owner_deselected and platform == 'macos-arm64':
            raw[-1]['selected_by_repository'] = False
            raw[-1]['gates'] = [{'unavailable': 'required owner prerequisite absent'}]
        directory = artifacts / f"coverage-go-{lane}-{platform}-{current['sha']}-{current['run_attempt']}"
        directory.mkdir()
        directories[(lane, platform)] = directory
        partition = owners.partition(raw, manifest, 'go', lane, platform, complete=platform == 'linux-x86_64')
        receipt = dict(domain='go', lane=lane, workers=6, dist='loadgroup', timeout_s=90, rss_bytes=runner.RSS,
                       group_policy={}, unattempted=[], unrun=[], collection_pending_modules=[],
                       raw_union_count=len(raw), lane_count=len(partition['local_nodeids']), batches=[])
        receipt['coverage'] = dict(schema=1, context=current, host=coverage.HOSTS[platform],
                                   manifest_sha256=sha, scope='domain' if platform == 'linux-x86_64' else 'owned-modules',
                                   **partition)
        receipt['transfers'] = [dict(row, status='TRANSFER_PENDING') for row in partition['transfers']]
        receipt['unrun'] = [dict(row, status='UNRUN_PREREQUISITE') for row in raw
                            if row['canonical_nodeid'] in partition['local_nodeids']
                            and not row['selected_by_repository']]
        for filename, value in [('source-before', source), ('source-after', source),
                ('ownership-manifest', manifest), ('modules', [module]), ('collection-plan', [[module]]),
                ('raw-inventory', {'modules': [module], 'rows': raw}), ('tools-setup-receipt', {}),
                ('tools-before', {'verified': [], 'claim': 'declared tool bytes only; execution results are separate'}),
                ('tools-after', {'verified': [], 'claim': 'declared tool bytes only; execution results are separate'}),
                ('required-tools-before', {'platform': platform, 'bindings': []}),
                ('required-tools-after', {'platform': platform, 'bindings': []})]:
            put(directory / (filename + '.json'), value)
        if platform == 'macos-arm64':
            invocation = '/model/bin/clang'
            tool = dict(status='AVAILABLE', invocation=invocation, resolved=invocation, sha256='d' * 64,
                        version_observation=dict(status='COMPLETE', exit_code=0, command=[invocation, '--version'],
                            stdout='model clang', stderr='', stdout_sha256=hashlib.sha256(b'model clang').hexdigest(),
                            stdout_base64=base64.b64encode(b'model clang').decode('ascii'), stderr_base64='',
                            stderr_sha256=hashlib.sha256(b'').hexdigest()))
            cpp_tool = copy.deepcopy(tool)
            cpp_tool.update(invocation='/model/bin/g++', resolved='/model/bin/g++')
            cpp_tool['version_observation']['command'][0] = '/model/bin/g++'
            put(directory / 'tools-setup-receipt.json', {'g++': cpp_tool, 'clang': tool})
            for phase in ('before', 'after'):
                put(directory / ('tools-' + phase + '.json'), {'verified': [
                    {'tool': 'g++', 'path': '/model/bin/g++', 'sha256': tool['sha256']},
                    {'tool': 'clang', 'path': invocation, 'sha256': tool['sha256']}],
                    'claim': 'declared tool bytes only; execution results are separate'})
                put(directory / ('required-tools-' + phase + '.json'), {'platform': platform, 'bindings': [
                    dict(tool='clang', invocation=invocation, resolved=invocation, sha256=tool['sha256'])]})
        collect = directory / 'collection-000'
        collect.mkdir()
        put(collect / 'modules.json', [module])
        raw_nodes = [row['nodeid'] for row in raw]
        selected = [row['nodeid'] for row in raw if row['selected_by_repository']]
        witness = dict(collect_only=True, workers=0, dist='no', marker='', local_schedule={'workers': 0, 'dist': 'no'},
                       requested_modules=[module], module_reports=[{'nodeid': module, 'outcome': 'passed'}],
                       collection_anomalies=[], rows=raw, raw_nodeids=raw_nodes, selected=selected)
        put(collect / 'inventory/controller.json', witness)
        collection_events = [dict(event='start', collect_only=True, maxfail=0,
            platform='linux' if platform == 'linux-x86_64' else 'darwin', machine=coverage.HOSTS[platform]['machine'])]
        deselected = [node for node in raw_nodes if node not in selected]
        if deselected:
            collection_events.append(dict(event='deselected', nodeids=deselected))
        collection_events += [dict(event='collected', nodeids=selected),
            dict(event='finish', exitstatus=0, testsfailed=0, testscollected=len(selected))]
        events(collect / 'live.jsonl', collection_events)
        guarded = dict(status='COMPLETE', timeout_s=90, max_tree_rss_bytes=runner.RSS,
                       peak_tree_rss_bytes=1000, returncode=0)
        put(collect / 'result.json', guarded)
        part = runner.summarize_collection(collect, [module], 0)
        receipt['collections'] = [dict(directory=collect.name, modules=[module], status=part['status'],
                                       returncode=0, raw_count=len(raw), selected_count=len(selected))]
        local = set(partition['local_nodeids'])
        selected_rows = [dict(row, selected_by_repository=row['selected_by_repository'] and row['canonical_nodeid'] in local) for row in raw]
        batches = runner.batches(selected_rows, lane)
        put(directory / 'batch-plan.json', [[row['canonical_nodeid'] for row in batch] for batch in batches])
        for index, batch in enumerate(batches):
            path = directory / f'batch-{index:03}'
            path.mkdir()
            selection = [row['canonical_nodeid'] for row in batch]
            put(path / 'selection.json', selection)
            execution = dict(collect_only=False, workers=6, dist='loadgroup', rows=raw,
                             raw_nodeids=raw_nodes, selected=selection)
            for worker in range(6):
                put(path / f'inventory/gw{worker}.json', execution)
            reports = [dict(event='start', maxfail=0, platform='linux' if platform == 'linux-x86_64' else 'darwin', machine=coverage.HOSTS[platform]['machine'])]
            for ident in selection:
                reports += [dict(event='report', nodeid=ident, when=phase, outcome='passed')
                            for phase in ('setup', 'call', 'teardown')]
            reports.append(dict(event='finish', exitstatus=0, testsfailed=0))
            events(path / 'live.jsonl', reports)
            put(path / 'result.json', guarded)
            summary = runner.summarize_batch(path, selection, raw)
            put(path / 'outcomes.json', summary)
            result = dict(directory=path.name, returncode=0, guard_error=None, **summary)
            proceed, reason = runner.batch_continuation(result)
            result.update(continue_independent_batches=proceed, continuation_reason=reason)
            receipt['batches'].append(result)
        receipt['status'] = runner.campaign_status(receipt)
        put(directory / 'receipt.json', receipt)
    return dict(root=root, environ=environ, plan=plan_path, artifacts=artifacts, directories=directories)


def join(data):
    return coverage.reconcile(data['plan'], data['artifacts'], root=data['root'], environ=data['environ'])


def test_modeled_two_owner_pass_uses_real_summary_functions(tmp_path, monkeypatch):
    data = model(tmp_path, monkeypatch)
    result = join(data)
    assert result['status'] == 'PASS', result
    reference = [row for row in result['obligations'] if row['lane'] == 'reference']
    assert {row['platform'] for row in reference} == {'linux-x86_64', 'macos-arm64'}
    assert all(row['evidence']['full_phase'] for row in reference)


def test_consistent_owner_deselection_remains_unresolved(tmp_path, monkeypatch):
    data = model(tmp_path, monkeypatch, owner_deselected=True)
    result = join(data)
    assert result['status'] == 'FAIL'
    assert any('INCOMPLETE_UNRUN' in error for error in result['errors'])
    assert any(row.get('platform') == 'macos-arm64' for row in result['unresolved'])


@pytest.mark.parametrize('fault', ['missing-owner', 'duplicate', 'unknown', 'sha', 'run_id', 'run_attempt',
    'host', 'static-function', 'static-groups', 'manifest-missing', 'manifest-wrong', 'stale-override', 'inventory', 'batch-plan', 'source',
    'tool', 'tool-binding', 'tool-unavailable', 'version-text', 'version-hash', 'version-bytes', 'version-encoding',
    'live-host', 'no-finish', 'guard-missing', 'timeout', 'rss', 'cleanup', 'phase', 'skip', 'xfail', 'xpass', 'subtest',
    'fake-pass', 'forged-summary', 'wheel-only', 'native-missing', 'native-unexpected', 'plan-omit-owner', 'plan-omit-lane'])
def test_incomplete_or_forged_models_never_green(tmp_path, monkeypatch, fault):
    data = model(tmp_path, monkeypatch)
    directory = data['directories'][('reference', 'macos-arm64')]
    batch = directory / 'batch-000'
    if fault in {'missing-owner', 'wheel-only'}:
        directory.rename(data['artifacts'] / 'wheel-PASS')
    elif fault in {'duplicate', 'unknown'}:
        import shutil
        shutil.copytree(directory, data['artifacts'] / ('coverage-' + fault))
    elif fault in {'sha', 'run_id', 'run_attempt'}:
        mutate(directory / 'receipt.json', lambda row: row['coverage']['context'].update({fault: '9' * 40 if fault == 'sha' else '99'}))
    elif fault == 'host':
        mutate(directory / 'receipt.json', lambda row: row['coverage'].update(host=coverage.HOSTS['linux-x86_64']))
    elif fault in {'static-function', 'static-groups'}:
        # Keep every local frozen/execution copy consistent. The full Linux
        # authority must still reject a changed nondefault static inventory.
        for path in [directory / 'raw-inventory.json', *directory.glob('*/inventory/*.json')]:
            def change(value):
                for row in value['rows']:
                    if row['function'] == 'test_reference':
                        row.update(function='test_changed') if fault == 'static-function' else row.update(groups=['changed'])
            mutate(path, change)
    elif fault == 'manifest-missing':
        (directory / 'ownership-manifest.json').unlink()
    elif fault == 'manifest-wrong':
        mutate(directory / 'receipt.json', lambda row: row['coverage'].update(manifest_sha256='0' * 64))
    elif fault == 'stale-override':
        mutate(data['root'] / owners.MANIFEST, lambda row: row['overrides'][0].update(nodeid='tests/go/test_model.py::test_absent'))
    elif fault == 'inventory':
        mutate(directory / 'raw-inventory.json', lambda row: row['rows'].pop())
    elif fault == 'batch-plan':
        put(directory / 'batch-plan.json', [])
    elif fault == 'source':
        mutate(directory / 'source-after.json', lambda row: row['files'].update({'test.py': 'c' * 64}))
    elif fault == 'tool':
        mutate(directory / 'tools-after.json', lambda row: row.update(verified=[{'tool': 'fake'}]))
    elif fault == 'tool-binding':
        mutate(directory / 'required-tools-after.json', lambda row: row['bindings'][0].update(sha256='e' * 64))
    elif fault == 'tool-unavailable':
        mutate(directory / 'tools-setup-receipt.json', lambda row: row['clang'].update(status='UNRUN_PREREQUISITE'))
    elif fault.startswith('version-'):
        change = {'version-text': {'stdout': 'different clang'},
                  'version-hash': {'stdout_sha256': 'e' * 64},
                  'version-bytes': {'stdout_base64': base64.b64encode(b'different clang').decode('ascii')},
                  'version-encoding': {'stdout_base64': 'invalid!'}}[fault]
        mutate(directory / 'tools-setup-receipt.json', lambda row: row['clang']['version_observation'].update(change))
    elif fault in {'live-host', 'no-finish'}:
        reports = [json.loads(line) for line in (batch / 'live.jsonl').read_text().splitlines()]
        if fault == 'live-host':
            reports[0].update(platform='linux', machine='x86_64')
        else:
            reports.pop()
        events(batch / 'live.jsonl', reports)
    elif fault == 'guard-missing':
        (batch / 'result.json').unlink()
    elif fault in {'timeout', 'rss', 'cleanup'}:
        change = {'timeout': {'timeout_s': 91}, 'rss': {'peak_tree_rss_bytes': runner.RSS + 1},
                  'cleanup': {'post_exit_cleanup_pids': [99]}}[fault]
        mutate(batch / 'result.json', lambda row: row.update(change))
    elif fault in {'phase', 'skip', 'xfail', 'xpass', 'subtest'}:
        reports = [json.loads(line) for line in (batch / 'live.jsonl').read_text().splitlines()]
        if fault == 'phase':
            reports.pop(-2)
        elif fault == 'skip':
            reports[2]['outcome'] = 'skipped'
        elif fault in {'xfail', 'xpass'}:
            reports[2].update(wasxfail='expected', outcome='skipped' if fault == 'xfail' else 'passed')
        else:
            reports.insert(-1, dict(event='subtest', nodeid=reports[2]['nodeid'], when='call', outcome='failed',
                subtest_index=1, report_type='_pytest.subtests.SubtestReport', subtest_context={'kwargs': {}, 'msg': None}))
        events(batch / 'live.jsonl', reports)
    elif fault == 'fake-pass':
        mutate(directory / 'receipt.json', lambda row: row.update(status='PASS', batches=[]))
    elif fault == 'forged-summary':
        mutate(batch / 'outcomes.json', lambda row: row['outcomes'][0].update(status='FORGED'))
    elif fault == 'native-missing':
        monkeypatch.setattr(coverage, 'requires_native_inputs', lambda domain, lane: lane == 'integration')
        mutate(data['plan'], lambda row: row.update(matrix_independent={'include': [x for x in row['matrix']['include'] if x['lane'] != 'integration']},
            matrix_native={'include': [{'domain': 'go', 'lane': 'integration'}]}, source_native=True))
    elif fault == 'native-unexpected':
        put(directory / 'native-inputs-before.json', {'status': 'PASS'})
    elif fault == 'plan-omit-owner':
        mutate(data['plan'], lambda row: row.update(matrix_platform={'include': []}, source_platform=False))
    else:
        mutate(data['plan'], lambda row: row['matrix']['include'].pop())
    result = join(data)
    assert result['status'] == 'FAIL', (fault, result)
    assert result['errors'] or result['unresolved']


def test_missing_plan_and_artifacts_returns_diagnostic(tmp_path):
    result = coverage.reconcile(tmp_path / 'missing.json', tmp_path / 'absent', environ={})
    assert result['status'] == 'FAIL' and result['errors']
