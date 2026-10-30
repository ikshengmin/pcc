"""Real bounded archive transport with synthetic ABI bytes; never native builds."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest

from scripts import ci_stage1_artifact_export as exporter
from scripts import ci_stage1_bundle as bundle
from tests.test_ci_stage1_artifact_export import _native_header

COMMIT = "a" * 40
SOURCE = "b" * 64
LEASE = {"repository": "owner/pcc", "workflow": ".github/workflows/pcc1-package-parity.yml",
         "run_id": 123, "run_attempt": 2, "sha": COMMIT}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


@pytest.fixture
def product(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("bundle contract must not execute subprocesses/native tools")
    for name in ("run", "Popen", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    for name in ("system", "fork", "forkpty", "posix_spawn", "posix_spawnp"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, forbidden)
    admitted = []
    monkeypatch.setattr(exporter, "_admit_runtime", lambda *args: admitted.append(args))

    def make(platform_id="linux-x86_64"):
        root = tmp_path / "producer"
        root.mkdir()
        paths, files = bundle.layout(platform_id)
        for name in files:
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"tiny existing fixture\n")
            p.chmod(0o644)
        compiler = root / paths["compiler"]
        compiler.write_bytes(_native_header(platform_id))
        compiler.chmod(0o755)
        source = {"commit": COMMIT, "source_sha256": SOURCE, "clean_commit": True}
        runtime = root / bundle.RUNTIME
        hashes = {suffix: sha(Path(str(runtime) + suffix).read_bytes())
                  for suffix in ("", ".provenance.json", ".capi_syms")}
        stage = {"phase": "stage1", "status": "PASS", "source": source,
                 "compiler": {"path": str(compiler), "sha256": sha(compiler.read_bytes())},
                 "runtime": {"path": str(runtime), "target": bundle.PLATFORMS[platform_id]["target"],
                             "config": bundle.CONFIG.copy(), "hashes": hashes}}
        save(root / bundle.STAGE, stage)
        if platform_id == "macos-arm64":
            save(root / bundle.PROFILE, {"schema": "pcc.bootstrap_stage_result.v1", "stage": 1,
                                       "output": str(compiler), "backend": "self", "returncode": 0,
                                       "publish_barrier_returncode": 0, "compile_wall_ms": 3})
        else:
            canary_name = "canary1" + (".exe" if platform_id == "windows-x86_64" else "")
            (root / bundle.SHARED / canary_name).write_bytes(_native_header(platform_id))
            (root / bundle.SHARED / canary_name).chmod(0o755)
            for name in ("stage1.log", "stage1.resources.jsonl", "canary1-compile.log", "canary1-compile.resources.jsonl", "canary1-run.resources.jsonl"):
                (root / bundle.SHARED / name).write_bytes(b"")
            shared = {"source": source, "compiler_path": str(compiler), "target": stage["runtime"]["target"],
                      "runtime": {"path": str(runtime), "sha256": hashes[""], "provenance_sha256": hashes[".provenance.json"], "capi_inventory_sha256": hashes[".capi_syms"]},
                      "stages": [{"stage": 1, "sha256": stage["compiler"]["sha256"], "canary": {
                          key: sha((root / bundle.SHARED / name).read_bytes()) for name, key in
                          (("canary.py", "source_sha256"), (canary_name, "executable_sha256"), ("canary1-run.log", "output_sha256"))}}]}
            save(root / paths["shared_receipt"], shared)
        originals = {name: (root / name).read_bytes() for name in files}
        manifest = exporter.export_bundle(root, commit=COMMIT, platform_id=platform_id, lease=LEASE.copy())
        assert admitted == [(root, runtime, stage["runtime"]["target"])]
        assert all((root / name).read_bytes() == data for name, data in originals.items())
        # Model a fresh job at the same absolute checkout path, without
        # rewriting any source receipt inside the transported bundle.
        for name in files:
            (root / name).unlink()
        return root / exporter.OUTPUT_DIRECTORY, root, manifest, originals
    return make


def repack(directory, edit):
    """Alter transport fixtures, never production/source receipts."""
    manifest = json.loads((directory / "manifest.json").read_text())
    with tarfile.open(directory / exporter.ARCHIVE_NAME) as archive:
        members = [(copy.copy(member), archive.extractfile(member).read()) for member in archive]
    edit(manifest, members)
    raw = (json.dumps(manifest) + "\n").encode()
    (directory / "manifest.json").write_bytes(raw)
    with tarfile.open(directory / exporter.ARCHIVE_NAME, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
        for member, content in members:
            if member.name == "manifest.json":
                content = raw
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


def admit(args, platform_id="linux-x86_64", lease=None):
    directory, root, _, _ = args
    return bundle.import_bundle(root, directory, COMMIT, platform_id, LEASE if lease is None else lease)


@pytest.mark.parametrize("platform_id", tuple(bundle.PLATFORMS))
def test_real_roundtrip_all_four_abi_layouts_preserves_original_receipts(product, platform_id):
    args = product(platform_id)
    directory, root, expected, originals = args
    actual = admit(args, platform_id)
    assert actual == expected
    assert actual["producer"] == LEASE and actual["platform"] == platform_id
    assert all((root / name).read_bytes() == value for name, value in originals.items())
    assert not list((root / "build").glob("pcc-marker-import-*"))
    if platform_id == "macos-arm64":
        assert actual["paths"]["shared_receipt"] is None
        assert actual["paths"]["compiler"] == "build/bootstrap/pcc1"
        assert bundle.PROFILE in actual["files"]
        assert not any(name.startswith(bundle.SHARED) for name in actual["files"])
    else:
        assert actual["paths"]["compiler"].endswith(".exe") == (platform_id == "windows-x86_64")
        assert actual["paths"]["shared_receipt"] == bundle.SHARED + "receipt.json"
    # The original producer paths are preserved, never rewritten or executed.
    receipt = json.loads((root / bundle.STAGE).read_text())
    assert receipt["compiler"]["path"] == str(root / actual["paths"]["compiler"])


@pytest.mark.parametrize("field,value", (("repository", "other/pcc"), ("workflow", ".github/workflows/other.yml"),
                                         ("run_id", 124), ("run_attempt", 3), ("sha", "c" * 40)))
def test_rejects_cross_run_attempt_workflow_repository_or_sha(product, field, value):
    args = product(); wrong = dict(LEASE, **{field: value})
    with pytest.raises(ValueError, match="lease"):
        admit(args, lease=wrong)
    assert not any((args[1]/name).exists() for name in args[2]["files"])


@pytest.mark.parametrize("platform_id", tuple(bundle.PLATFORMS))
def test_exact_headers_reject_wrong_machine_class_and_truncation(tmp_path, platform_id):
    data = bytearray(_native_header(platform_id));p=tmp_path/'compiler'
    p.write_bytes(data);bundle.verify_native_header(p, platform_id)
    machine_offset = 18 if platform_id.startswith('linux') else 4 if platform_id == 'macos-arm64' else 132
    data[machine_offset] ^= 1;p.write_bytes(data)
    with pytest.raises(ValueError, match="machine|ABI"):
        bundle.verify_native_header(p, platform_id)
    p.write_bytes(_native_header(platform_id)[:20])
    with pytest.raises(ValueError):
        bundle.verify_native_header(p, platform_id)


@pytest.mark.parametrize("kind", ("symlink", "hardlink", "directory", "fifo", "pax", "sparse", "traversal", "absolute", "duplicate", "extra", "missing", "hash", "mode", "embedded_manifest"))
def test_rejects_malformed_archives_before_publishing(product, kind):
    args = product();directory,root,manifest,_=args
    def edit(m, members):
        index = next(i for i,(h,d) in enumerate(members) if h.name == manifest['paths']['compiler'])
        header,content=members[index]
        types={'symlink':tarfile.SYMTYPE,'hardlink':tarfile.LNKTYPE,'directory':tarfile.DIRTYPE,
               'fifo':tarfile.FIFOTYPE,'pax':tarfile.XHDTYPE,'sparse':tarfile.GNUTYPE_SPARSE}
        if kind in types: header.type=types[kind];header.linkname='../../outside'
        elif kind=='traversal':header.name='../outside'
        elif kind=='absolute':header.name='/outside'
        elif kind=='duplicate':members.append((copy.copy(header),content))
        elif kind=='extra':extra=copy.copy(header);extra.name='build/extra';members.append((extra,content))
        elif kind=='missing':members.pop(index)
        elif kind=='hash':members[index]=(header,content[:-1]+b'X')
        elif kind=='mode':header.mode=0o4777
        elif kind=='embedded_manifest':
            # The external copy will differ; leave the tar copy at its old bytes.
            return
    repack(directory,edit)
    if kind=='embedded_manifest':
        raw=(directory/'manifest.json').read_text();(directory/'manifest.json').write_text(raw+' ')
    with pytest.raises((ValueError,tarfile.TarError)):
        admit(args)
    assert not any((root/name).exists() for name in manifest['files'])
    assert not list((root/'build').glob('pcc-marker-import-*'))


@pytest.mark.parametrize('size', (-1, True, 2**31+1))
def test_rejects_invalid_or_oversized_manifest_sizes(product,size):
    args=product();directory,root,manifest,_=args
    manifest['files'][manifest['paths']['compiler']]['size']=size
    save(directory/'manifest.json',manifest)
    with pytest.raises(ValueError,match='metadata'):
        admit(args)
    assert not root.joinpath(manifest['paths']['compiler']).exists()


@pytest.mark.parametrize('kind',('existing','parent_symlink','file_symlink'))
def test_refuses_overwrite_and_symlink_destinations(product,kind,tmp_path):
    args=product();directory,root,manifest,_=args;p=root/manifest['paths']['compiler']
    if kind=='parent_symlink':
        p.parent.rmdir()
        p.parent.symlink_to(tmp_path/'outside',target_is_directory=True)
    else:
        p.parent.mkdir(parents=True,exist_ok=True)
        if kind=='existing':p.write_bytes(b'preserve')
        else:p.symlink_to(tmp_path/'missing')
    with pytest.raises(ValueError,match='destination|escapes'):
        admit(args)
    if kind=='existing':assert p.read_bytes()==b'preserve'
    assert not list(root.glob('build/pcc-marker-import-*'))


def test_tampered_receipt_rehashed_in_bundle_still_rejected(product):
    args=product()
    def edit(manifest,members):
        for index,(header,content) in enumerate(members):
            if header.name==bundle.STAGE:
                stage=json.loads(content);stage['status']='FAIL';content=json.dumps(stage).encode()
                members[index]=(header,content)
                manifest['files'][bundle.STAGE].update(sha256=sha(content),size=len(content))
    repack(args[0],edit)
    with pytest.raises(ValueError,match='PASS Stage1'):
        admit(args)
    assert not (args[1]/bundle.STAGE).exists()


def test_workflow_lease_is_canonical_and_requires_every_binding():
    env={'GITHUB_REPOSITORY':'owner/pcc','GITHUB_WORKFLOW_REF':'owner/pcc/.github/workflows/pcc1-package-parity.yml@refs/heads/master',
         'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'2','GITHUB_SHA':COMMIT}
    assert bundle.workflow_lease(env)==LEASE
    for key in env:
        broken=dict(env);del broken[key]
        with pytest.raises(ValueError):bundle.workflow_lease(broken)
    for value in ('0','-1','1.0','bad'):
        with pytest.raises(ValueError):bundle.workflow_lease(dict(env,GITHUB_RUN_ATTEMPT=value))


@pytest.mark.parametrize('system,machine,expected',(('linux','x86_64','linux-x86_64'),('linux','aarch64','linux-aarch64'),('darwin','arm64','macos-arm64'),('win32','AMD64','windows-x86_64')))
def test_native_host_mapping(monkeypatch,system,machine,expected):
    monkeypatch.setattr(bundle.sys,'platform',system);monkeypatch.setattr(bundle.platform,'machine',lambda:machine)
    assert bundle.host_platform()==expected


def test_import_rejects_relocated_checkout_without_rewriting_receipts(product,tmp_path):
    directory,root,manifest,originals=product()
    relocated=tmp_path/'relocated';relocated.mkdir()
    with pytest.raises(ValueError,match='relocation is not admitted'):
        bundle.import_bundle(relocated,directory,COMMIT,'linux-x86_64',LEASE)
    assert not list(relocated.iterdir())
