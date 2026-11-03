"""Admit this run's existing Stage1 inputs for shared source tests; never build."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

try:
    from . import ci_stage1_bundle as bundle
except ImportError:
    import ci_stage1_bundle as bundle


def source_identity():
    try:
        from .bootstrap_platform import source_identity as current
    except ImportError:
        from bootstrap_platform import source_identity as current
    return current()


def verify_runtime(archive):
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive
    return _verified_test_runtime_archive(archive, threads=True)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def admit(root, directory, *, domain, environ=None):
    """Reuse strict transport admission, then expose only explicit fixture inputs.

    PCC_TEST_COMPILER is deliberately not set: the host-PCC source lane and its
    explicit module/pcc1 parameters must retain their distinct execution owners.
    """
    env = os.environ if environ is None else environ
    if domain != 'shared' or directory is None:
        raise ValueError('shared source validation requires an explicit same-run Stage1 bundle')
    if bundle.host_platform() != 'linux-x86_64':
        raise ValueError('shared source native inputs require Linux x86-64')
    if any(env.get(name) for name in (
            'PCC_CURRENT_PCC1', 'PCC_TEST_COMPILER', 'PCC_RUNTIME_ARCHIVE',
            'PCC_THREADED_RUNTIME_ARCHIVE', 'PCC_CI_STAGE1_MANIFEST',
            'PCC_CI_STAGE1_MANIFEST_SHA256')):
        raise ValueError('refuse inherited native source-test input overrides')
    root, directory = Path(root).resolve(strict=True), Path(directory).resolve(strict=True)
    source = source_identity()
    lease = bundle.workflow_lease(env)
    if source.get('commit') != lease['sha'] or source.get('clean_commit') is not True:
        raise ValueError('source native inputs require this clean event revision')
    manifest_path = directory / 'manifest.json'
    manifest_sha = digest(manifest_path)
    manifest = bundle.import_bundle(root, directory, source['commit'], 'linux-x86_64', lease)
    if source['source_sha256'] != manifest['source']['source_sha256']:
        raise ValueError('native input compiler/runtime source digest differs')
    runtime = root / manifest['paths']['runtime']
    compiler = root / manifest['paths']['compiler']
    checked_runtime, _provenance = verify_runtime(runtime)
    if checked_runtime != runtime:
        raise ValueError('native input runtime verification selected another archive')
    state = {'source': source, 'lease': lease, 'bundle': str(directory),
             'manifest_sha256': manifest_sha, 'compiler': str(compiler),
             'runtime': str(runtime), 'owner': 'same_workflow_stage1_producer'}
    updates = {'PCC_RUNTIME_ARCHIVE': str(runtime), 'PCC_THREADED_RUNTIME_ARCHIVE': str(runtime),
               'PCC_CURRENT_PCC1': str(compiler), 'PCC_REQUIRE_CURRENT_PCC1': '1',
               'PCC_NO_AUTO_PCC1': '1', 'PCC_TEST_NO_NATIVE_PROVISIONING': '1',
               'PCC_CI_STAGE1_MANIFEST': str(manifest_path),
               'PCC_CI_STAGE1_MANIFEST_SHA256': manifest_sha}
    return state, updates


def verify_after(root, state):
    """Recheck all transported bytes, receipts and runtime provenance after use."""
    root = Path(root).resolve(strict=True)
    directory = Path(state['bundle']).resolve(strict=True)
    if digest(directory / 'manifest.json') != state['manifest_sha256']:
        raise ValueError('source native input manifest changed during campaign')
    if source_identity() != state['source']:
        raise ValueError('source native input checkout changed during campaign')
    manifest = bundle.admit_in_place(root, directory, state['source']['commit'],
                                     'linux-x86_64', state['lease'])
    if manifest['source']['source_sha256'] != state['source']['source_sha256']:
        raise ValueError('source native input digest changed during campaign')
    runtime = root / manifest['paths']['runtime']
    checked_runtime, _provenance = verify_runtime(runtime)
    if str(checked_runtime) != state['runtime']:
        raise ValueError('source native input runtime changed during campaign')
    return state
