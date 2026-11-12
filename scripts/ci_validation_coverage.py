"""Reconcile same-run source owners from raw guarded evidence, never status labels.

Downloaded artifacts are evidence, not the obligation inventory. This performs
no test/compiler execution and never attempts to reopen a remote host's tools.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import tomllib

try:
    from . import ci_validation_run as runner
    from . import ci_validation_owners as owners
    from .ci_validation_plan import requires_native_inputs
except ImportError:
    import ci_validation_run as runner
    import ci_validation_owners as owners
    from ci_validation_plan import requires_native_inputs

ROOT = Path(__file__).resolve().parents[1]
HOSTS = {"linux-x86_64": {"platform": "linux-x86_64", "system": "Linux", "machine": "x86_64"},
         "macos-arm64": {"platform": "macos-arm64", "system": "Darwin", "machine": "arm64"}}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text(), object_pairs_hook=owners._unique_object)


def digest(value, size=64):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{' + str(size) + '}', value) is not None


def context(environ):
    value = {key: environ.get('GITHUB_' + key.upper(), '')
             for key in ('repository', 'sha', 'run_id', 'run_attempt')}
    require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value['repository']) is not None
            and digest(value['sha'], 40)
            and all(re.fullmatch(r'[1-9][0-9]*', value[key]) for key in ('run_id', 'run_attempt')),
            'missing/invalid current workflow context')
    return value


def validate_plan(plan, manifest, manifest_sha, current):
    require(type(plan.get('schema')) is int and plan['schema'] == 1, 'invalid plan schema')
    require(plan.get('context') == current and plan.get('after') == current['sha'], 'stale plan context')
    require(plan.get('ownership_manifest_sha256') == manifest_sha, 'plan ownership manifest differs')
    domains = plan.get('domains')
    require(isinstance(domains, list) and domains and domains == sorted(set(domains))
            and set(domains) <= owners.DOMAINS, 'invalid plan domains')
    expected = [dict(domain=domain, lane=lane) for domain in domains for lane in runner.LANES]
    require(plan.get('matrix') == {'include': expected} and plan.get('source') is bool(expected),
            'default matrix does not cover all lanes')
    for field, predicate in (('independent', False), ('native', True)):
        rows = [row for row in expected if requires_native_inputs(**row) is predicate]
        require(plan.get('matrix_' + field) == {'include': rows}
                and plan.get('source_' + field) is bool(rows), 'native/independent matrix differs')
    extra = owners.additional_campaigns(domains, manifest)
    require(plan.get('matrix_platform') == {'include': extra}
            and plan.get('source_platform') is bool(extra), 'platform matrix differs from tracked owners')
    return [dict(row, platform=manifest['default_platform']) for row in expected] + extra


def guard(directory, allowed):
    result = read(directory / 'result.json')
    require(result.get('status') == 'COMPLETE' and result.get('timeout_s') == runner.TIMEOUT
            and result.get('max_tree_rss_bytes') == runner.RSS
            and type(result.get('peak_tree_rss_bytes')) is int
            and 0 <= result['peak_tree_rss_bytes'] <= runner.RSS
            and type(result.get('returncode')) is int and result['returncode'] in allowed
            and ('post_exit_cleanup_pids' not in result or result['post_exit_cleanup_pids'] == []), 'incomplete/resource/cleanup guard: ' + directory.name)
    return result['returncode']


def live_host(directory, host):
    events = [json.loads(line) for line in (directory / 'live.jsonl').read_text().splitlines()]
    starts = [event for event in events if event.get('event') == 'start']
    require(len(starts) == 1 and events[0] == starts[0] and starts[0].get('platform') == ('linux' if host['system'] == 'Linux' else 'darwin')
            and starts[0].get('machine') == host['machine'],
            'live observed host differs: ' + directory.name)


def tool_evidence(directory, obligations, platform):
    setup = read(directory / 'tools-setup-receipt.json')
    before = read(directory / 'tools-before.json')
    require(before == read(directory / 'tools-after.json'), 'tool closure drift')
    expected = []
    for name, row in setup.items():
        require(isinstance(row, dict), 'invalid setup tool')
        if name == 'rust_installation':
            if 'installed_files' in row:
                files = row['installed_files']
                require(isinstance(files, list) and files and all(isinstance(item, dict) and isinstance(item.get('path'), str)
                        and digest(item.get('sha256')) for item in files)
                        and len({item['path'] for item in files}) == len(files),
                        'invalid Rust installation closure')
                expected.append({'tool': name, 'verified_files': len(files),
                                 'claim': 'installation bytes only; no source certification'})
            continue
        if 'invocation' in row:
            require(digest(row.get('sha256')) and isinstance(row.get('resolved'), str), 'invalid tool identity')
            expected.append({'tool': name, 'path': row['invocation'], 'sha256': row['sha256']})
    verified = before.get('verified')
    require(isinstance(verified, list) and all(isinstance(row, dict) and isinstance(row.get('tool'), str)
            for row in verified) and len({row['tool'] for row in verified}) == len(verified),
            'duplicate/malformed tool verification')
    require(set(before) == {'verified', 'claim'}
            and before['claim'] == 'declared tool bytes only; execution results are separate'
            and {row['tool']: row for row in verified} == {row['tool']: row for row in expected},
            'tool verification does not match setup')
    names = sorted({name for row in obligations if row['platform'] == platform for name in row['tools']})
    bindings = []
    for name in names:
        row = setup.get(name, {})
        observation = row.get('version_observation', {})
        require(row.get('status') == 'AVAILABLE' and observation.get('status') == 'COMPLETE'
                and type(observation.get('exit_code')) is int and observation['exit_code'] == 0
                and observation.get('command', [None])[0] == row.get('invocation'), 'required tool unavailable: ' + name)
        for stream in ('stdout', 'stderr'):
            require(isinstance(observation.get(stream), str)
                    and isinstance(observation.get(stream + '_base64'), str)
                    and digest(observation.get(stream + '_sha256')),
                    'invalid version observation')
            data = base64.b64decode(observation[stream + '_base64'], validate=True)
            require(hashlib.sha256(data).hexdigest() == observation[stream + '_sha256']
                    and data.decode('utf-8', 'replace') == observation[stream],
                    'version observation bytes/hash/text differ')
        bindings.append(dict(tool=name, **{key: row[key] for key in ('invocation', 'resolved', 'sha256')}))
    required = {'platform': platform, 'bindings': bindings}
    require(read(directory / 'required-tools-before.json') == required
            and read(directory / 'required-tools-after.json') == required, 'required PATH tool binding differs')
    rust_before = directory / 'consumed-rust-receipt-before.json'
    consumed = setup.get('rust_installation', {}).get('targets', {}).get('x86_64-unknown-linux-gnu', {}).get('status') == 'AVAILABLE'
    if consumed or rust_before.exists() or (directory / 'consumed-rust-receipt-after.json').exists():
        require(read(rust_before) == read(directory / 'consumed-rust-receipt-after.json'), 'consumed Rust receipt drift')
        require(digest(read(rust_before).get('sha256')) and isinstance(read(rust_before).get('path'), str)
                and setup.get('rust_installation'),
                'consumed Rust receipt identity missing')


def native_evidence(directory, receipt, domain, lane, platform, current):
    needed = requires_native_inputs(domain, lane)
    paths = [directory / ('native-inputs-' + stage + '.json') for stage in ('before', 'after')]
    if not needed:
        require('native_inputs' not in receipt and not any(path.exists() for path in paths), 'unexpected native inputs')
        return
    require(platform == 'linux-x86_64', 'native input owner must be Linux')
    state = read(paths[0])
    require(state == read(paths[1]) == receipt.get('native_inputs'), 'native input closure missing/drifted')
    lease = dict(current, run_id=int(current['run_id']), run_attempt=int(current['run_attempt']),
                 workflow='.github/workflows/pcc1-package-parity.yml')
    require(state.get('lease') == lease and state.get('owner') == 'same_workflow_stage1_producer'
            and state.get('source', {}).get('commit') == current['sha']
            and state['source'].get('clean_commit') is True and digest(state['source'].get('source_sha256'))
            and digest(state.get('manifest_sha256')) and all(isinstance(state.get(key), str) and state[key]
                for key in ('bundle', 'compiler', 'runtime')), 'native input admission identity differs')


def campaign(artifact, item, manifest, manifest_sha, current, source, root):
    domain, lane, platform = (item[key] for key in ('domain', 'lane', 'platform'))
    directory = artifact
    receipt = read(directory / 'receipt.json')
    require(receipt.get('domain') == domain and receipt.get('lane') == lane, 'campaign identity differs')
    require(receipt.get('workers') == 6 and receipt.get('dist') == 'loadgroup'
            and receipt.get('timeout_s') == runner.TIMEOUT and receipt.get('rss_bytes') == runner.RSS,
            'campaign budgets/schedule differ')
    require(not any(receipt.get(key) for key in ('error', 'native_inputs_error', 'tool_closure_error',
            'ownership_closure_error', 'unattempted', 'collection_pending_modules')), 'unfinished/failed producer')
    require(read(directory / 'source-before.json') == source == read(directory / 'source-after.json'), 'source drift')
    complete = platform == manifest['default_platform']
    files = runner.modules(domain, root) if complete else owners.owner_modules(manifest, domain, lane, platform)
    require(read(directory / 'modules.json') == files, 'module inventory changed')
    shards = runner.collection_plan(files)
    require(read(directory / 'collection-plan.json') == shards, 'collection plan changed')
    require(sorted(path.name for path in directory.glob('collection-*') if path.is_dir())
            == [f'collection-{index:03}' for index in range(len(shards))], 'collection directories differ')
    parts, declarations = [], []
    for index, shard in enumerate(shards):
        collect = directory / f'collection-{index:03}'
        require(read(collect / 'modules.json') == shard, 'collection shard changed')
        live_host(collect, HOSTS[platform])
        code = guard(collect, (0, 5))
        part = runner.summarize_collection(collect, shard, code)
        parts.append(part)
        declarations.append(dict(directory=collect.name, modules=shard, status=part['status'],
                                 returncode=code, raw_count=len(part['rows']), selected_count=part['selected_count']))
    require(receipt.get('collections') == declarations, 'collection declarations differ from evidence')
    rows = runner.merge_collections(parts, files)
    require(read(directory / 'raw-inventory.json') == {'modules': files, 'rows': rows}, 'raw inventory changed')
    partition = owners.partition(rows, manifest, domain, lane, platform, complete=complete)
    coverage = dict(schema=1, context=current, host=HOSTS[platform], manifest_sha256=manifest_sha,
                    scope='domain' if complete else 'owned-modules', **partition)
    require(read(directory / 'ownership-manifest.json') == manifest, 'frozen ownership manifest differs')
    require(receipt.get('transfers') == [dict(row, status='TRANSFER_PENDING') for row in partition['transfers']],
            'transfer declarations differ')
    require(receipt.get('coverage') == coverage, 'coverage declaration differs from tracked owners/raw evidence')
    tool_evidence(directory, partition['obligations'], platform)
    native_evidence(directory, receipt, domain, lane, platform, current)
    config = tomllib.loads((root / 'pyproject.toml').read_text())
    resources = runner.resource_exclusion_groups(config, domain)
    require(receipt.get('group_policy') == {name: 'resource-exclusion-only' for name in resources}, 'group policy changed')
    if not complete:
        resources = tuple(group for group in resources if any(group in row['groups'] for row in rows))
    local = set(partition['local_nodeids'])
    selection = [dict(row, selected_by_repository=row['selected_by_repository'] and row['canonical_nodeid'] in local)
                 for row in rows]
    batches = runner.batches(selection, lane, resource_groups=resources)
    plan = [[row['canonical_nodeid'] for row in batch] for batch in batches]
    require(read(directory / 'batch-plan.json') == plan, 'batch plan differs')
    require(sorted(path.name for path in directory.glob('batch-*') if path.is_dir())
            == [f'batch-{index:03}' for index in range(len(plan))], 'batch directories differ')
    results = []
    for index, (batch, expected) in enumerate(zip(batches, plan)):
        path = directory / f'batch-{index:03}'
        require(read(path / 'selection.json') == expected, 'batch selection differs')
        live_host(path, HOSTS[platform])
        code = guard(path, (0, 1))
        files = {row['file'] for row in batch}
        require(sorted(item.name for item in (path / 'inventory').glob('*.json'))
                == [f'gw{worker}.json' for worker in range(6)], 'six exact worker inventories required')
        summary = runner.summarize_batch(path, expected, [row for row in rows if row['file'] in files])
        require(read(path / 'outcomes.json') == summary, 'stored outcomes differ from raw phases')
        result = dict(directory=path.name, returncode=code, guard_error=None, **summary)
        proceed, reason = runner.batch_continuation(result)
        require(proceed, 'batch continuation failed: ' + reason)
        result.update(continue_independent_batches=proceed, continuation_reason=reason)
        results.append(result)
    unrun = [dict(row, status='UNRUN_PREREQUISITE') for row in rows
             if row['canonical_nodeid'] in local and not row['selected_by_repository']]
    require(receipt.get('batches') == results and receipt.get('unrun') == unrun
            and receipt.get('raw_union_count') == len(rows) and receipt.get('lane_count') == len(local),
            'campaign summary differs from reconstructed evidence')
    status = runner.campaign_status(dict(receipt, batches=results, unrun=unrun))
    require(status in {'PASS', 'NOT_APPLICABLE', 'PLATFORM_COVERAGE_PENDING'}, 'campaign did not complete: ' + status)
    require(receipt.get('status') == status, 'producer status differs from recomputed status')
    outcomes = {row['nodeid']: row for result in results for row in result['outcomes']}
    require(set(outcomes) == local and all(row['status'] == 'PASS' for row in outcomes.values()),
            'local obligations lack complete PASS')
    return {'rows': rows, 'partition': partition, 'outcomes': outcomes, 'artifact': artifact.name}


def reconcile(plan_path, artifacts, *, root=ROOT, environ=None):
    result = {'schema': 1, 'status': 'FAIL', 'errors': [], 'obligations': [], 'unresolved': [], 'campaigns': []}
    try:
        current = context(os.environ if environ is None else environ)
        result['context'] = current
        manifest, manifest_sha = owners.read_manifest(root)
        result['manifest_sha256'] = manifest_sha
        planned = validate_plan(read(plan_path), manifest, manifest_sha, current)
        source = runner.identity()
        require(source.get('sha') == current['sha'], 'checkout differs from plan source')
        expected = {f"coverage-{item['domain']}-{item['lane']}-{item['platform']}-{current['sha']}-{current['run_attempt']}": item
                    for item in planned}
        actual = {path.name: path for path in artifacts.iterdir() if path.name.startswith('coverage-')}
        for name in sorted(set(actual) - set(expected)):
            result['errors'].append('unexpected/stale/duplicate producer: ' + name)
        accepted = {}
        for name, item in expected.items():
            key = tuple(item[field] for field in ('domain', 'lane', 'platform'))
            try:
                require(name in actual and actual[name].is_dir(), 'missing producer: ' + name)
                accepted[key] = campaign(actual[name], item, manifest, manifest_sha, current, source, root)
                result['campaigns'].append(dict(item, artifact=name, status='COMPLETE'))
            except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
                result['errors'].append(name + ': ' + str(error))
                result['unresolved'].append(dict(item, status='UNRESOLVED', reason=str(error)))
        for domain in read(plan_path)['domains']:
            inventories = [witness['rows'] for key, witness in accepted.items()
                           if key[0] == domain and key[2] == manifest['default_platform']]
            def stable_inventory(rows):
                return [{key: value for key, value in row.items() if key not in {'selected_by_repository', 'gates'}} for row in rows]
            require(all(stable_inventory(rows) == stable_inventory(inventories[0]) for rows in inventories),
                    'default lane raw inventory drift')
            for lane in runner.LANES:
                authority = accepted.get((domain, lane, manifest['default_platform']))
                if authority is None:
                    continue
                for obligation in authority['partition']['obligations']:
                    key = (domain, lane, obligation['platform'])
                    witness = accepted.get(key)
                    node = obligation['nodeid']
                    valid = witness is not None and node in witness['outcomes']
                    row = dict(obligation, status='PASS' if valid else 'UNRESOLVED')
                    if valid:
                        row.update(artifact=witness['artifact'], evidence=witness['outcomes'][node])
                    else:
                        result['unresolved'].append(row)
                    result['obligations'].append(row)
                for key, witness in accepted.items():
                    if key[:2] != (domain, lane) or key[2] == manifest['default_platform']:
                        continue
                    wanted = [row for row in authority['partition']['obligations'] if row['platform'] == key[2]]
                    observed = [row for row in witness['partition']['obligations'] if row['platform'] == key[2]]
                    require(wanted == observed, 'nondefault inventory differs from full-domain owner obligations')
                    owned_files = {row['file'] for row in witness['rows']}
                    def metadata(rows):
                        return [{k: v for k, v in row.items() if k not in {'nodeid', 'selected_by_repository', 'gates'}}
                                for row in rows if row['file'] in owned_files]
                    require(metadata(authority['rows']) == metadata(witness['rows']), 'cross-platform raw inventory drift')
        if not result['errors'] and not result['unresolved']:
            result['status'] = 'PASS'
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
        result['errors'].append(type(error).__name__ + ': ' + str(error))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--artifacts', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args(argv)
    result = reconcile(args.plan, args.artifacts)
    runner.save(args.out, result)
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
