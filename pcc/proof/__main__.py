"""Command line: ``python -m pcc.proof check PATH...``."""

from __future__ import annotations

import os
import sys

from pcc.proof.session import CheckError, Session

USAGE = "usage: python -m pcc.proof check PATH [PATH ...]"


def _python_files(path):
    if os.path.isdir(path):
        found = []
        for directory, names, files in os.walk(path):
            names[:] = sorted(name for name in names if name != "__pycache__")
            for name in sorted(files):
                if name.endswith(".py"):
                    found.append(os.path.join(directory, name))
        return found
    return [path]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] != "check":
        print(USAGE, file=sys.stderr)
        return 2
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 100000))
    session = Session()
    try:
        for argument in argv[1:]:
            for path in _python_files(argument):
                session.check_file(path)
    except CheckError as error:
        print(str(error), file=sys.stderr)
        return 1
    total = 0
    for module in sorted(session.reports):
        _, names = session.reports[module]
        total += len(names)
        print(f"{module}: {len(names)} declarations")
    print(f"checked {total} declarations in {len(session.reports)} modules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
