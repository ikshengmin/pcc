"""Unified CI contracts; miniature pytest bodies are real, not native PCC proof."""

from contextlib import nullcontext
from copy import deepcopy
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap

import pytest

from scripts import ci_pytest_markers as gate


def _frozen_baseline_checkout_contract():
    workflow = (gate.ROOT / '.github/workflows/pcc1-package-parity.yml').read_text()
    job = workflow.split('\n  pytest-markers:\n', 1)[1]
    frozen = job.split('      # Immutable comparison source, never an executable baseline compiler.\n', 1)[1]
    guard, following = frozen.split('      - uses: actions/checkout@v5\n', 1)
    assert guard.startswith('      - name: Refuse inherited Git runtime configuration before frozen checkout\n')
    assert '        shell: bash\n' in guard
    script = textwrap.dedent(guard.split('        run: |\n', 1)[1])
    checkout = '      - uses: actions/checkout@v5\n' + following.split('\n      - ', 1)[0]
    pairs = re.findall(r'^          (GIT_CONFIG_[A-Z_0-9]+): "([^"\n]*)"$', checkout, re.M)
    assert len(pairs) == 5 and len(dict(pairs)) == 5
    return script, dict(pairs), checkout


def _isolated_checkout_environment(tmp_path, autocrlf='true'):
    # Only fixture children see these files; never read/write the user's Git config.
    env = {name: value for name, value in os.environ.items() if not name.startswith('GIT_')}
    home = tmp_path / 'home'
    home.mkdir()
    global_config = home / 'global.gitconfig'
    system_config = home / 'system.gitconfig'
    global_config.write_text(
        '[core]\n\tautocrlf = ' + autocrlf + '\n\teol = crlf\n'
        '[fixture]\n\tglobal = preserved-global\n', encoding='utf-8')
    system_config.write_text('[fixture]\n\tsystem = preserved-system\n', encoding='utf-8')
    env.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home / 'xdg'),
               GIT_CONFIG_GLOBAL=str(global_config), GIT_CONFIG_SYSTEM=str(system_config))
    return env, (global_config, system_config)


def _fixture_git(directory, env, *args):
    return subprocess.run(['git', '-C', str(directory), *args], env=env,
                          capture_output=True, check=True, timeout=30).stdout


@pytest.mark.parametrize('autocrlf', ['true', 'false'])
def test_frozen_baseline_checkout_preserves_exact_bytes_with_runner_conversion(tmp_path, autocrlf):
    """Real Git checkouts, not a mocked digest or a native compiler diagnostic."""
    assert shutil.which('git') and shutil.which('bash'), 'Git and Bash are required by the workflow'
    script, overrides, _ = _frozen_baseline_checkout_contract()
    fixture = gate.ROOT / 'tests/fixtures/ci_pytest_markers/integer_fold_contract.85b0.b64'
    original = base64.b64decode(b''.join(fixture.read_bytes().split()), validate=True)
    assert len(original) == 6576
    assert hashlib.sha256(original).hexdigest() == gate.FROZEN_SHA256
    assert b'\r' not in original and original.count(b'\n') == 217
    env, config_files = _isolated_checkout_environment(tmp_path, autocrlf)
    config_bytes = {path: path.read_bytes() for path in config_files}
    seed = tmp_path / 'seed'
    seed.mkdir()
    _fixture_git(seed, env, 'init', '--quiet')
    frozen = seed / gate.FROZEN_SOURCE
    frozen.parent.mkdir(parents=True)
    frozen.write_bytes(original)
    _fixture_git(seed, env, '-c', 'core.autocrlf=false', 'add', '--', gate.FROZEN_SOURCE)
    _fixture_git(seed, env, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '--quiet', '-m', 'Frozen byte fixture')
    head = _fixture_git(seed, env, 'rev-parse', 'HEAD').strip()
    assert _fixture_git(seed, env, 'show', 'HEAD:' + gate.FROZEN_SOURCE) == original
    guard = subprocess.run(['bash', '-c', script], cwd=tmp_path, env=env,
                           capture_output=True, text=True, timeout=10)
    assert guard.returncode == 0 and not guard.stdout and not guard.stderr
    for label, checkout_env in [('ordinary', env), ('guarded', dict(env, **overrides))]:
        checkout = tmp_path / label
        # Local filesystem source only; no network, force/reset, or user checkout.
        _fixture_git(tmp_path, env, 'clone', '--quiet', '--no-checkout', str(seed), str(checkout))
        local_config = checkout / '.git/config'
        before_local = local_config.read_bytes()
        _fixture_git(checkout, checkout_env, 'checkout', '--quiet', '--detach', head.decode('ascii'))
        actual = (checkout / gate.FROZEN_SOURCE).read_bytes()
        if label == 'ordinary' and autocrlf == 'true':
            assert actual == original.replace(b'\n', b'\r\n')
            assert gate.digest(checkout / gate.FROZEN_SOURCE) != gate.FROZEN_SHA256
        else:
            assert actual == original
            assert gate.digest(checkout / gate.FROZEN_SOURCE) == gate.FROZEN_SHA256
        assert _fixture_git(checkout, checkout_env, 'rev-parse', 'HEAD').strip() == head
        assert _fixture_git(checkout, checkout_env, 'config', '--get', 'fixture.global').strip() == b'preserved-global'
        assert _fixture_git(checkout, checkout_env, 'config', '--get', 'fixture.system').strip() == b'preserved-system'
        assert _fixture_git(checkout, env, 'config', '--get', 'core.autocrlf').strip() == autocrlf.encode('ascii')
        assert local_config.read_bytes() == before_local
        assert all(path.read_bytes() == data for path, data in config_bytes.items())


@pytest.mark.parametrize(('name', 'value'), [
    ('GIT_CONFIG_COUNT', ''),
    ('GIT_CONFIG_COUNT', '0'),
    ('GIT_CONFIG_COUNT', '1'),
    ('GIT_CONFIG_KEY_0', 'fixture.runtime'),
    ('GIT_CONFIG_VALUE_17', 'SENTINEL_RUNTIME_SECRET'),
    ('GIT_CONFIG_KEY_malformed', 'SENTINEL_RUNTIME_SECRET'),
    ('GIT_CONFIG_VALUE_malformed', 'SENTINEL_RUNTIME_SECRET'),
    ('GIT_CONFIG_PARAMETERS', "'fixture.runtime'='SENTINEL_RUNTIME_SECRET'"),
], ids=['empty-count', 'zero-count', 'nonzero-count', 'orphan-key', 'orphan-value',
        'malformed-key', 'malformed-value', 'legacy-parameters'])
def test_frozen_baseline_guard_refuses_inherited_runtime_config_without_values(tmp_path, name, value):
    assert shutil.which('bash'), 'Bash is required by the workflow'
    script, _, _ = _frozen_baseline_checkout_contract()
    env, config_files = _isolated_checkout_environment(tmp_path)
    config_bytes = {path: path.read_bytes() for path in config_files}
    env[name] = value
    result = subprocess.run(['bash', '-c', script], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert result.stdout == ('::error::Inherited Git runtime configuration is present; '
                             'refusing frozen-source checkout override.\n')
    assert result.stderr == ''
    assert name not in result.stdout and 'SENTINEL_RUNTIME_SECRET' not in result.stdout
    assert all(path.read_bytes() == data for path, data in config_bytes.items())


def test_complete_marker_entry_real_six_worker_execution(tmp_path, monkeypatch):
    # Real pytest processes: default six workers, actual test bodies, and both
    # explicit markers. Never invoke a PCC compiler, runtime builder or probe.
    corpus = tmp_path / "corpus"
    (corpus / "tests").mkdir(parents=True)
    (corpus / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "-n 6 --dist=loadgroup -m \'not integration\'"\n'
        'markers = ["integration: native group"]\n')
    (corpus / "conftest.py").write_text(
        'def pytest_collection_modifyitems(config, items):\n'
        '    dropped = [item for item in items if item.name == "test_unavailable"]\n'
        '    if dropped:\n'
        '        config.hook.pytest_deselected(items=dropped)\n'
        '        items[:] = [item for item in items if item not in dropped]\n')
    (corpus / "tests/test_selection.py").write_text(
        'from pathlib import Path\nimport pytest\n'
        '@pytest.mark.xdist_group("host_group")\n'
        'def test_host(): Path("host-ran").write_text("actual body")\n'
        'def test_unavailable(): raise AssertionError("gate did not deselect")\n'
        '@pytest.mark.integration\n'
        '@pytest.mark.xdist_group("native_group")\n'
        'def test_native_marker(): Path("integration-ran").write_text("actual body")\n'
        '@pytest.mark.integration\n@pytest.mark.skip(reason="hardware fixture control")\n'
        'def test_skipped(): raise AssertionError("skip control")\n')
    for marker, sentinel, count in (("not integration", "host-ran", 1), ("integration", "integration-ran", 2)):
        out = corpus / marker.replace(" ", "-")
        out.mkdir()
        command = gate.marker_command(out, marker)
        watchdog = gate.watchdog_command(out, marker, "linux-x86_64")
        assert watchdog[watchdog.index("--") + 1:] == command
        assert "--no-performance-lock" in watchdog and "--performance-lock" not in watchdog
        assert watchdog[watchdog.index("--max-tree-rss-bytes") + 1] == "17179869184"
        assert watchdog[watchdog.index("--timeout") + 1] == str(gate.TIMEOUTS[marker])
        assert command[-3:] == ["-m", marker, "tests"]
        assert not any(option in command for option in ("-n", "-n0", "-o", "--collect-only", "--noconftest", "-k", "--ignore"))
        env = dict(os.environ, PYTHONPATH=str(gate.ROOT), PCC_MARKER_REPORT_DIR=str(out))
        env.pop("PYTEST_ADDOPTS", None)
        result = subprocess.run(command, cwd=corpus, env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "created: 6/6 workers" in result.stdout
        assert "LoadGroupScheduling" in result.stdout
        assert (corpus / sentinel).read_text() == "actual body"
        summary = gate.summarize(out, marker)
        rows = json.loads((out / "inventory-gw0.json").read_text())
        assert any("@" in name for name in rows["selected"]), "exercise actual loadgroup nodeid rewriting"
        assert not rows["unexpected"]
        assert summary["status"] == "PASS_WITH_GAPS" and summary["selected_count"] == count
        assert bool(summary["gated_out"]) == (marker == "not integration")
        assert bool(summary["skipped_or_xfailed"]) == (marker == "integration")
        if marker == "not integration":
            assert not (corpus / "integration-ran").exists()
        inventory = out / "inventory-gw0.json"
        old = inventory.read_text()
        inventory.write_text("{}")
        with pytest.raises(ValueError, match="worker selection differs"):
            gate.summarize(out, marker)
        inventory.write_text(old)
        config = out / "configuration.json"
        config.write_text(json.dumps({"marker": marker, "workers": 0, "dist": "loadgroup"}))
        with pytest.raises(ValueError, match="marker/default scheduling"):
            gate.summarize(out, marker)


def test_unified_workflow_covers_both_groups_on_four_native_platforms(tmp_path, monkeypatch):
    workflow = (gate.ROOT / '.github/workflows/pcc1-package-parity.yml').read_text()
    assert not (gate.ROOT / '.github/workflows/pytest-markers.yml').exists()
    assert 'workflow_run:' not in workflow and 'pull_request_target:' not in workflow
    job = workflow.split('\n  pytest-markers:\n', 1)[1]
    assert 'needs: [ci-plan, other-platform-native-wheel, pcc1-package-parity]' in job
    assert "if: ${{ !cancelled() && needs.ci-plan.result == 'success' }}" in job
    assert 'run-id:' not in job and 'github-token:' not in job  # current run only
    assert 'ref: ${{ github.sha }}' in job and 'persist-credentials: false' in job
    guard, checkout_env, baseline_checkout = _frozen_baseline_checkout_contract()
    assert 'ref: 85b0e08cd28e143d87607a5cd76e8f7c350cabd7' in baseline_checkout
    assert 'path: build/pytest-marker-baseline' in baseline_checkout
    assert 'sparse-checkout: ' + gate.FROZEN_SOURCE in baseline_checkout
    assert 'sparse-checkout-cone-mode: false' in baseline_checkout
    assert 'persist-credentials: false' in baseline_checkout
    assert 'GIT_CONFIG_COUNT:' not in workflow.replace(baseline_checkout, '')
    assert 'git config' not in guard
    assert checkout_env == {
        'GIT_CONFIG_COUNT': '2',
        'GIT_CONFIG_KEY_0': 'core.autocrlf',
        'GIT_CONFIG_VALUE_0': 'false',
        'GIT_CONFIG_KEY_1': 'core.eol',
        'GIT_CONFIG_VALUE_1': 'lf',
    }
    assert 'contents: read' in job and 'actions: read' in job
    assert 'matrix.marker' in job and 'fromJSON(needs.ci-plan.outputs.markers)' in job
    include = re.search(r"include: \$\{\{ inputs.full_chain && fromJSON\('(.+?)'\)", job)
    assert include is not None
    extra = json.loads(include.group(1))
    assert extra == [{'platform': {'id': 'macos-arm64', 'runner': 'macos-15'}, 'marker': 'integration'}]
    assert "|| fromJSON('[]')" in include.string.splitlines()[include.string[:include.start()].count('\n')]
    for platform_id, runner in (
        ('macos-arm64', 'macos-15'), ('linux-x86_64', 'ubuntu-24.04'),
        ('linux-aarch64', 'ubuntu-24.04-arm'), ('windows-x86_64', 'windows-2022'),
    ):
        assert '{ id: ' + platform_id + ', runner: ' + runner + ' }' in job
    assert '--no-install-project' in job and '--collect-only' not in job
    assert job.count('            build/pytest-markers/**/pcc1-routing.jsonl\n') == 1
    assert '--platform ${{ matrix.platform.id }}' in job
    assert 'stage1-native-${{ matrix.platform.id }}-${{ github.sha }}-${{ github.run_attempt }}' in job
    assert 'cancel-in-progress: false' in workflow and 'queue: max' in workflow
    assert gate.TIMEOUTS == {'not integration': 2400, 'integration': 1800}
    assert "--phase stage1${{ inputs.full_chain && ' --full-chain' || '' }}" in workflow
    assert 'PCC_BOOTSTRAP_STAGE_TIMEOUT: "7200"' in workflow
    assert 'timeout-minutes: 360' in workflow and 'timeout-minutes: 200' in workflow
    # Preserve existing independent Stage1/preflight graph, including invalid
    # manual configuration rejection before any existing heavy job can start.
    existing = workflow.split('\n  macos-pcc0-regressions:\n', 1)[1].split('\n  pytest-markers:\n', 1)[0]
    assert '\n    needs:' not in existing
    assert existing.count("if: ${{ !inputs.full_chain || inputs.pytest_selection == 'not integration' }}") == 7
    expected_jobs = {
        'macos-pcc0-regressions', 'macos-pcc0-float-doc-regressions',
        'macos-pcc0-indexed-split', 'worker-prior-preflight',
        'windows-rss-publication', 'other-platform-native-wheel', 'pcc1-package-parity',
    }
    job_ids = set(re.findall(r'^  ([a-z][a-z0-9-]*):$', workflow.split('jobs:\n', 1)[1], re.M))
    assert job_ids == expected_jobs | {'ci-plan', 'pytest-markers'}
    predicate = "    if: ${{ !inputs.full_chain || inputs.pytest_selection == 'not integration' }}"
    for name in expected_jobs:
        # Job-level if is independent of ci-plan status and precedes all steps.
        block = workflow.split('\n  ' + name + ':\n', 1)[1]
        assert block.splitlines()[0] == predicate
    for selection in ('both', 'integration'):
        assert not (not True or selection == 'not integration')
    nonmac = workflow.split('\n  other-platform-native-wheel:', 1)[1].split('\n  pcc1-package-parity:', 1)[0]
    export = nonmac.split('      - name: Export the admitted native Stage1 identity', 1)[1].split('      - name: Verify the compiled sibling', 1)[0]
    assert "matrix.platform == 'linux-x86_64'" not in export
    assert 'steps.platform_qualification.outcome' in export
    mac = workflow.split('\n  pcc1-package-parity:', 1)[1].split('\n  pytest-markers:', 1)[0]
    assert mac.index('Preserve the existing native Stage1 bundle') < mac.index('Verify the new pcc1 on native prior worker shapes')
    assert '--platform macos-arm64' in mac
    plan = textwrap.dedent(workflow.split('        run: |\n', 1)[1].split('\n  macos-pcc0-regressions:', 1)[0])
    output = tmp_path / 'github-output'
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    for full_chain, selection, expected in (
        ('false', 'both', ['not integration', 'integration']),
        ('false', 'integration', ['integration']),
        ('false', 'not integration', ['not integration']),
        ('true', 'not integration', ['not integration']),
    ):
        output.unlink(missing_ok=True)
        monkeypatch.setenv('FULL_CHAIN', full_chain)
        monkeypatch.setenv('PYTEST_SELECTION', selection)
        exec(compile(plan, '<workflow plan>', 'exec'), {})
        assert json.loads(output.read_text().split('=', 1)[1]) == expected
        platforms = re.findall(r'- \{ id: ([a-z0-9_-]+), runner: ([a-z0-9.-]+) \}', job)
        assert len(platforms) == 4
        expanded = [(platform_id, marker) for platform_id, _ in platforms for marker in expected]
        if full_chain == 'true':
            expanded += [(row['platform']['id'], row['marker']) for row in extra]
            assert len(expanded) == len(set(expanded)) == 5
            assert [platform_id for platform_id, marker in expanded if marker == 'integration'] == ['macos-arm64']
        elif selection == 'both':
            assert len(expanded) == len(set(expanded)) == 8
        if full_chain == 'true' or selection == 'both':
            for platform_id, _ in platforms:
                owners = []
                if full_chain == 'true' and platform_id != 'macos-arm64':
                    owners.append('producer --full-chain')
                if (platform_id, 'integration') in expanded:
                    assert '--partition remainder --owner-report build/pytest-bootstrap-owner' in job
                    owners.append('platform-local bootstrap pytest')
                assert len(owners) == 1, (platform_id, owners)
    for selection in ('both', 'integration'):
        output.unlink(missing_ok=True)
        monkeypatch.setenv('FULL_CHAIN', 'true')
        monkeypatch.setenv('PYTEST_SELECTION', selection)
        with pytest.raises(SystemExit, match='requires pytest_selection'):
            exec(compile(plan, '<workflow plan>', 'exec'), {})
        assert not output.exists()


def test_platform_watchdogs_keep_original_limits_and_propagate_failure(tmp_path, monkeypatch):
    for platform_id in ('linux-x86_64', 'linux-aarch64', 'macos-arm64'):
        command = gate.watchdog_command(tmp_path, 'integration', platform_id)
        assert command[command.index('--timeout') + 1] == '1800'
        assert '--no-performance-lock' in command
        if platform_id == 'macos-arm64':
            assert command[command.index('--auto-tree-rss-ceiling-bytes') + 1] == '4294967296'
            assert command[command.index('--min-tree-rss-bytes') + 1] == '2147483648'
            assert command[command.index('--darwin-preflight-reserve-bytes') + 1] == '536870912'
            assert '--max-tree-rss-bytes' not in command
        else:
            assert command[command.index('--max-tree-rss-bytes') + 1] == '17179869184'
    with pytest.raises(ValueError, match='Job Object'):
        gate.watchdog_command(tmp_path, 'integration', 'windows-x86_64')
    from scripts import platform_process_watchdog
    calls = []
    def windows_guard(command, **options):
        calls.append((command, options))
        assert options['timeout'] == 1800 and options['rss_limit'] == 17179869184
        assert options['env']['PCC_WORKER_TREE_BUDGET_BYTES'] == '17179869184'
        return {'command': command, 'peak_tree_rss': 123, 'seconds': 0.2}
    monkeypatch.setattr(platform_process_watchdog, 'run', windows_guard)
    gate.run_guarded(tmp_path, 'integration', {}, 'windows-x86_64')
    assert len(calls) == 1
    assert json.loads((tmp_path / 'result.json').read_text())['status'] == 'COMPLETE'
    def timeout(*args, **kwargs):
        raise TimeoutError('native Job timeout')
    monkeypatch.setattr(platform_process_watchdog, 'run', timeout)
    with pytest.raises(TimeoutError):
        gate.run_guarded(tmp_path, 'integration', {}, 'windows-x86_64')
    assert json.loads((tmp_path / 'result.json').read_text())['status'] == 'FAIL'


def test_current_host_watchdog_executes_real_pytest_body(tmp_path, monkeypatch):
    """Each CI OS exercises its real watchdog, not just a mocked OS branch."""
    corpus = tmp_path / 'corpus'
    (corpus / 'tests').mkdir(parents=True)
    # Run the original sampler and its real support closure, with only the
    # working directory changed to this miniature project.
    original_root = gate.ROOT
    original_watchdog = gate.watchdog_command
    def watchdog(*args, **kwargs):
        command = original_watchdog(*args, **kwargs)
        command[1] = str(original_root / 'scripts/run_process_tree_sample.py')
        return command
    monkeypatch.setattr(gate, 'watchdog_command', watchdog)
    (corpus / 'pyproject.toml').write_text('[tool.pytest.ini_options]\naddopts="-n 6 --dist=loadgroup"\n')
    (corpus / 'tests/test_actual.py').write_text(
        'from pathlib import Path\ndef test_actual(): Path("executed").write_text("real body")\n')
    monkeypatch.setattr(gate, 'ROOT', corpus)
    out = tmp_path / 'guard'
    out.mkdir()
    env = dict(os.environ, PYTHONPATH=str(original_root), PCC_MARKER_REPORT_DIR=str(out))
    env.pop('PYTEST_ADDOPTS', None)
    gate.run_guarded(out, 'not integration', env, gate.host_platform())
    assert (corpus / 'executed').read_text() == 'real body'
    assert gate.summarize(out, 'not integration')['status'] == 'PASS'


def test_shared_input_copies_original_receipt_without_symlink_or_build(tmp_path):
    relative = 'build/platform-qualification/shared/receipt.json'
    source = tmp_path / relative
    source.parent.mkdir(parents=True)
    raw = b'{"original":"preserved"}\n'
    source.write_bytes(raw)
    manifest = {'paths': {'shared_receipt': relative},
                'files': {relative: {'sha256': hashlib.sha256(raw).hexdigest()}}}
    gate.prepare_shared_input(tmp_path, manifest)
    copy = tmp_path / 'build/platform-bootstrap/shared/receipt.json'
    assert copy.read_bytes() == raw and not copy.parent.is_symlink()
    with pytest.raises(ValueError, match='existing bootstrap'):
        gate.prepare_shared_input(tmp_path, manifest)


def test_nonmac_full_gc_refuses_hidden_stage1_rebuild(tmp_path, monkeypatch):
    from scripts import bootstrap_platform
    from pcc.frontends.python import pipeline_targets
    monkeypatch.setattr(bootstrap_platform, '_same_run_admission', lambda *a, **kw: nullcontext())
    monkeypatch.setattr(bootstrap_platform, 'performance_lock', nullcontext)
    monkeypatch.setenv('PCC_TEST_NO_NATIVE_PROVISIONING', '1')
    def forbidden(*args, **kwargs):
        raise AssertionError('must not rebuild Stage1')
    monkeypatch.setattr(bootstrap_platform, 'run_chain', forbidden)
    with pytest.raises(RuntimeError, match='automatic rebuild is disabled'):
        bootstrap_platform.run_from_shared('0', tmp_path / 'gc0')


def test_macos_full_gc_fixture_uses_explicit_admitted_input(tmp_path, monkeypatch):
    from tests.python import test_pcc_bootstrap_full as full
    compiler, runtime, manifest = (tmp_path / name for name in ('pcc1', 'runtime.a', 'manifest.json'))
    compiler.write_bytes(b'fixture only, never executed')
    compiler.chmod(0o755)
    linkage_checks = []
    monkeypatch.setattr(full, '_links_libpython', lambda path: linkage_checks.append(path) or False)
    monkeypatch.setattr(full, '_is_macos_arm64', lambda: True)
    monkeypatch.setenv('PCC_TEST_NO_NATIVE_PROVISIONING', '1')
    monkeypatch.setenv('PCC_CI_STAGE1_MANIFEST', str(manifest))
    calls = []
    def admitted(root, path, archive):
        calls.append((root, path, archive))
        return compiler
    monkeypatch.setattr(gate, 'admitted_stage1', admitted)
    def forbidden(*args):
        raise AssertionError('must not rebuild Stage1')
    monkeypatch.setattr(full, '_build_shared_stage1', forbidden)
    assert full.shared_stage1_pcc1.__wrapped__(runtime) == compiler
    assert calls == [(full._REPO_ROOT, manifest, runtime)]
    assert linkage_checks == [compiler]
    monkeypatch.setattr(full, '_links_libpython', lambda path: True)
    with pytest.raises(AssertionError, match='unexpectedly links libpython'):
        full.shared_stage1_pcc1.__wrapped__(runtime)
    monkeypatch.delenv('PCC_CI_STAGE1_MANIFEST')
    with pytest.raises(pytest.fail.Exception, match='automatic rebuild is disabled'):
        full.shared_stage1_pcc1.__wrapped__(runtime)


def test_admitted_fixture_rechecks_lease_source_and_runtime_bytes(tmp_path, monkeypatch):
    from scripts import bootstrap_platform
    from tests import runtime_fixture_provenance
    source = {'commit': 'a' * 40, 'source_sha256': 'b' * 64, 'clean_commit': True}
    lease = {'repository': 'ikshengmin/pcc', 'run_id': 123, 'run_attempt': 1}
    compiler_rel, runtime_rel = 'build/bootstrap/pcc1', 'build/runtime.a'
    compiler, runtime = tmp_path / compiler_rel, tmp_path / runtime_rel
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(b'explicit compiler fixture, not executed')
    runtime.write_bytes(b'explicit runtime fixture, not executed')
    manifest = {'producer': lease, 'platform': 'macos-arm64', 'source': source,
                'paths': {'compiler': compiler_rel, 'runtime': runtime_rel},
                'files': {name: {'sha256': gate.digest(tmp_path / name)}
                          for name in (compiler_rel, runtime_rel)}}
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest))
    monkeypatch.setenv('PCC_CI_STAGE1_MANIFEST_SHA256', gate.digest(path))
    monkeypatch.setattr(gate, 'workflow_lease', lambda: lease)
    monkeypatch.setattr(gate, 'host_platform', lambda: 'macos-arm64')
    monkeypatch.setattr(bootstrap_platform, 'source_identity', lambda: source)
    admissions = []
    monkeypatch.setattr(runtime_fixture_provenance, '_verified_test_runtime_archive',
                        lambda archive, threads: admissions.append((archive, threads)))
    assert gate.admitted_stage1(tmp_path, path, runtime) == compiler
    assert admissions == [(runtime, True)]
    for key, value in (('producer', dict(lease, run_attempt=2)),
                       ('platform', 'linux-aarch64'),
                       ('source', dict(source, commit='c' * 40))):
        bad = deepcopy(manifest)
        bad[key] = value
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            gate.admitted_stage1(tmp_path, path, runtime)
    path.write_text(json.dumps(manifest))
    compiler.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='bytes differ'):
        gate.admitted_stage1(tmp_path, path, runtime)


def test_nonmac_shared_stage1_still_executes_requested_later_stages(tmp_path, monkeypatch):
    """A contract fixture checks dispatch only; never calls it fixed-point proof."""
    from scripts import bootstrap_platform
    source, configuration = {'commit': 'a' * 40}, {'target': 'native'}
    compiler = tmp_path / 'existing-pcc1'
    compiler.write_bytes(b'existing Stage1 contract fixture')
    shared = {'source': source, 'configuration': configuration,
              'compiler_path': str(compiler), 'stages': [{'sha256': gate.digest(compiler)}]}
    common = tmp_path / 'shared'
    common.mkdir()
    (common / 'receipt.json').write_text(json.dumps(shared))
    monkeypatch.setattr(bootstrap_platform, '_same_run_admission', lambda *a, **kw: nullcontext())
    monkeypatch.setattr(bootstrap_platform, 'performance_lock', nullcontext)
    monkeypatch.setattr(bootstrap_platform, 'source_identity', lambda *a: source)
    monkeypatch.setattr(bootstrap_platform, 'build_configuration', lambda *a: configuration)
    monkeypatch.setenv('PCC_TEST_NO_NATIVE_PROVISIONING', '1')
    calls = []
    def chain(gc, directory, **options):
        calls.append((gc, directory, options))
        return {'dispatch_only': True}
    monkeypatch.setattr(bootstrap_platform, 'run_chain', chain)
    result = bootstrap_platform.run_from_shared('4', tmp_path / 'gc4', stage_limit=3)
    assert result == {'dispatch_only': True} and len(calls) == 1
    assert calls[0][0] == '4' and calls[0][2]['stage_limit'] == 3
    assert calls[0][2]['shared'] == shared


def test_workflow_script_entrypoints_import_without_pythonpath(tmp_path):
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    for name in ('ci_pytest_markers.py', 'ci_stage1_artifact_export.py'):
        result = subprocess.run([sys.executable, str(gate.ROOT / 'scripts' / name), '--help'],
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert '--platform' in result.stdout


def test_manual_full_chain_keeps_shared_export_stage1_only(tmp_path, monkeypatch):
    """Exercise real matrix orchestration, not native/fixed-point execution."""
    from scripts import bootstrap_platform
    source = {'commit': 'a' * 40}
    shared = {'source': source, 'stages': [{'stage': 1}], 'qualified': False}
    calls = []
    def chain(gc, directory, **options):
        calls.append((gc, Path(directory), options))
        Path(directory).mkdir(parents=True, exist_ok=True)
        if Path(directory).name == 'shared':
            assert options['stage_limit'] == 1 and 'shared' not in options
            receipt = shared
        else:
            assert options['stage_limit'] == 3 and options['shared'] is shared
            receipt = {'gc': gc, 'stages': [{'stage': n} for n in (1, 2, 3)],
                       'qualified': True, 'contract_fixture_only': True}
        (Path(directory) / 'receipt.json').write_text(json.dumps(receipt))
        return receipt
    monkeypatch.setattr(bootstrap_platform, 'run_chain', chain)
    monkeypatch.setattr(bootstrap_platform, 'source_identity', lambda *a: source)
    result = bootstrap_platform.run_matrix(['0', '1', '2', '3', '4'], tmp_path,
                                           stage_limit=3, lock_held=True)
    assert len(calls) == 6
    assert sum(path.name == 'shared' for _, path, _ in calls) == 1
    assert {gc for gc, path, _ in calls if path.name != 'shared'} == {'0', '1', '2', '3', '4'}
    exported_shared = json.loads((tmp_path / 'shared/receipt.json').read_text())
    assert exported_shared == shared and exported_shared['stages'] == [{'stage': 1}]
    assert all([stage['stage'] for stage in chain['stages']] == [1, 2, 3]
               for chain in result['chains'])
