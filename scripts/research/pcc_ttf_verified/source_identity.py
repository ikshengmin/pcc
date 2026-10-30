"""Offline source receipts for research; never infer a revision from a label."""

import hashlib
from pathlib import Path
import re
import sys


def git_blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def observe_source(source_root, manifest=None):
    """Check actual files/import roots, optionally against a trusted Git manifest.

    Without a caller-supplied manifest the revision remains unknown. The caller
    obtains a manifest from a fixed-revision Git tree; this offline helper checks
    its contents, not remote authenticity. It does not read Git history.
    """
    root = Path(source_root).resolve()
    if not (root / "pcc" / "__init__.py").is_file():
        raise ValueError("source root must contain the actual PCC package")
    actual = {}
    for path in sorted((root / "pcc").rglob("*")):
        if "__pycache__" in path.parts or not path.is_file():
            continue
        if not path.resolve().is_relative_to(root / "pcc"):
            raise ValueError("source file escaped the selected PCC root")
        actual[str(path.relative_to(root))] = git_blob(path.read_bytes())
    revision = None
    if manifest is not None:
        revision = manifest.get("revision", "")
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("manifest needs a full source revision")
        expected = manifest.get("files")
        if not isinstance(expected, dict) or not expected:
            raise ValueError("manifest needs PCC file identities")
        for name, identity in expected.items():
            path = Path(name)
            if (not path.parts or path.is_absolute() or path.parts[0] != "pcc"
                    or ".." in path.parts or not isinstance(identity, str)
                    or not re.fullmatch(r"[0-9a-f]{40}", identity)):
                raise ValueError("invalid PCC source manifest entry")
        if actual != expected:
            differences = sorted(name for name in actual.keys() | expected.keys()
                                 if actual.get(name) != expected.get(name))
            raise ValueError("PCC source identity mismatch: " + ", ".join(differences[:10]))
    for name, module in tuple(sys.modules.items()):
        if name == "pcc" or name.startswith("pcc."):
            filename = getattr(module, "__file__", None)
            if filename and not Path(filename).resolve().is_relative_to(root / "pcc"):
                raise ValueError("PCC already imported from a different source root")
    return {"revision": revision, "root": str(root),
            "verified_source_files": len(actual), "files": actual}
