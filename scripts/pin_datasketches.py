"""Pin an unchanged Apache DataSketches release into projects/.

Usage (development only; never run by tests):

    python -I scripts/pin_datasketches.py KEY ARCHIVE TREE_JSON [--output DIR]

KEY selects one entry of RELEASES. ARCHIVE is the GitHub tag archive
(codeload.github.com/apache/<repo>/tar.gz/refs/tags/<tag>) and TREE_JSON the
saved `GET /repos/apache/<repo>/git/trees/<commit>?recursive=1` response.
Every archive member is extracted with the tarfile data filter, compared with
its Git blob ID in the commit tree, and copied unchanged; files that
upstream's export-ignore drops from archives are recorded, not fetched. The
lock (and so the directory) is a pure function of the archive and the tree:
rerunning on the same inputs reproduces it byte for byte.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.datasketches_sources import PROJECTS, RELEASES, blob_id, symlink_target  # noqa: E402


def read_archive(archive):
    """{path: (kind, bytes)} for one tag archive, kind 'file' or 'symlink'."""
    members = {}
    with tarfile.open(fileobj=archive, mode="r:gz") as tar:
        prefix = None
        for member in tar.getmembers():
            member = tarfile.data_filter(member, "/nonexistent-pcc-destination")
            name = PurePosixPath(member.name)
            if prefix is None:
                prefix = name.parts[0]
            if name.parts[0] != prefix:
                raise ValueError("archive has more than one top-level directory")
            relative = PurePosixPath(*name.parts[1:]).as_posix()
            if member.isdir() or relative == ".":
                continue
            if member.issym():
                members[relative] = ("symlink", member.linkname.encode("utf-8"))
            elif member.isfile():
                members[relative] = ("file", tar.extractfile(member).read())
            else:
                raise ValueError("unexpected archive member type: " + member.name)
    return members


def build(key, archive, tree_json, output):
    release = RELEASES[key]
    tree = json.loads(Path(tree_json).read_text())
    if tree["truncated"] or tree["sha"] != release["revision"]:
        raise ValueError("tree listing is truncated or not the release commit's tree")
    expected = {item["path"]: item for item in tree["tree"] if item["type"] == "blob"}
    raw = Path(archive).read_bytes()
    members = read_archive(io.BytesIO(raw))
    for path, (kind, data) in members.items():
        item = expected.get(path)
        if item is None or blob_id(data) != item["sha"]:
            raise ValueError("archive member differs from the release tree: " + path)
        if (kind == "symlink") != (item["mode"] == "120000"):
            raise ValueError("archive member kind differs from the release tree: " + path)
    excluded = sorted(set(expected) - set(members))
    if output.exists():
        raise ValueError("output directory already exists: " + str(output))
    output.mkdir(parents=True)
    files = []
    for path in sorted(members):
        kind, data = members[path]
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        entry = {"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                 "git_blob": blob_id(data)}
        if kind == "symlink":
            if symlink_target(path, data.decode("utf-8")) not in members:
                raise ValueError("symbolic link does not name a pinned file: " + path)
            target.symlink_to(data.decode("utf-8"))
            entry["symlink"] = True
        else:
            target.write_bytes(data)
        files.append(entry)
    lock = {
        "schema_version": 1,
        "project": release["repository"].rsplit("/", 1)[-1],
        "version": release["version"],
        "repository": release["repository"],
        "tag": release["tag"],
        "tag_object": release["tag_object"],
        "revision": release["revision"],
        "license": "Apache-2.0",
        "license_files": ["LICENSE", "NOTICE"],
        "archive": {"url": release["repository"].replace("https://github.com/", "https://codeload.github.com/")
                    + "/tar.gz/refs/tags/" + release["tag"],
                    "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
        "selection": ("Every member of the release tag archive, unchanged (symbolic links kept as links); "
                      "each Git blob ID was checked against the release commit tree. Paths upstream marks "
                      "export-ignore are absent from archives and listed in export_ignored, not fetched."),
        "export_ignored": excluded,
        "complete_build_closure": False,
        "dependencies": release["dependencies"],
        "total_bytes": sum(entry["size"] for entry in files),
        "files": files,
    }
    # Byte preservation is projects/.gitattributes: an upstream root
    # .gitattributes (datasketches-go has one) is a pinned file itself.
    (output / "pcc-source-lock.json").write_text(json.dumps(lock, indent=2, ensure_ascii=True) + "\n")
    return lock


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("key", choices=sorted(RELEASES))
    parser.add_argument("archive")
    parser.add_argument("tree_json")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    output = arguments.output or ROOT / "projects" / PROJECTS[arguments.key]
    lock = build(arguments.key, arguments.archive, arguments.tree_json, output)
    print(f"{output}: {len(lock['files'])} files, {lock['total_bytes']} bytes, "
          f"{len(lock['export_ignored'])} export-ignored")


if __name__ == "__main__":
    main()
