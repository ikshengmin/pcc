"""pcc.stdlib.pathlib — narrow ``PurePath`` / ``Path``.

Uses ``os.path`` under the hood for the join/split logic. The full
Pathlib has ~60 methods; this scaffold covers the 15 or so pcc's
source and build scripts touch.
"""
from __future__ import annotations

import os
from os import path as _op


class PurePath(os.PathLike):
    def __init__(self, path="", *extra) -> None:
        raw = str(path)
        for part in extra:
            raw = _op.join(raw, str(part))
        self._raw = raw

    def __str__(self) -> str:

        return self._raw

    def __fspath__(self) -> str:
        return self._raw

    def __repr__(self) -> str:
        return f"PurePath({self._raw!r})"

    def __truediv__(self, other) -> "PurePath":
        # ``self.__class__`` and not ``PurePath``: CPython preserves the subclass
        # through every combining operation, so ``Path(root) / "pcc"`` stays a
        # ``Path``.  Hard-coding the base class here dropped every filesystem
        # method from the result -- ``bootstrap_source_sha256`` builds its
        # paths this way and died on ``'PurePath' object has no attribute
        # 'is_file'``.  Compiled, that AttributeError was swallowed by
        # ``codegen_checksum``'s ``except Exception``, which answered
        # "unknown", which the fail-closed staleness rule read as a stale
        # archive -- so pcc1 rejected every runtime archive it was given.
        return self.__class__(_op.join(self._raw, str(other)))

    @property
    def name(self) -> str:
        return _op.basename(self._raw)

    @property
    def parent(self) -> "PurePath":
        return self.__class__(_op.dirname(self._raw))

    def _parent_raw_paths(self) -> list:
        """Ancestor path strings, closest first, as CPython orders them.

        ``/a/b/c`` -> ``["/a/b", "/a", "/"]``; ``a/b/c`` -> ``["a/b", "a",
        "."]``; ``c`` -> ``["."]``; ``.``, ``/`` and ``""`` -> ``[]``.
        """
        out: list = []
        current = self._raw
        if current == "" or current == ".":
            return out
        while True:
            parent = _op.dirname(current)
            if parent == current:
                break
            if parent == "":
                out.append(".")
                break
            out.append(parent)
            current = parent
        return out

    @property
    def parents(self) -> list:
        """The ancestors, closest first.

        CPython returns a lazy sequence; a list is returned here because
        indexing and ``len`` are the whole surface callers use --
        ``pcc/package/inspect.py`` opens with
        ``Path(__file__).resolve().parents[2]``, which was a module-level
        CPython fallback while this was missing, and module-level code is
        outside the strict no-libpython stub projection.
        """
        out: list = []
        for raw in self._parent_raw_paths():
            out.append(PurePath(raw))
        return out

    @property
    def suffix(self) -> str:
        n = self.name
        i = n.rfind(".")
        if i <= 0:
            return ""
        return n[i:]

    @property
    def stem(self) -> str:
        n = self.name
        i = n.rfind(".")
        if i <= 0:
            return n
        return n[:i]

    def with_suffix(self, suffix: str) -> "PurePath":
        base, _old = _op.splitext(self._raw)
        return self.__class__(base + suffix)

    def with_name(self, name: str) -> "PurePath":
        parent = _op.dirname(self._raw)
        if parent:
            return self.__class__(_op.join(parent, name))
        return self.__class__(name)

    def relative_to(self, other) -> "PurePath":
        """``self`` expressed relative to ``other``, or ``ValueError``.

        ``pcc/driver/bootstrap_cache_identity.py`` builds its source-set keys from
        ``path.relative_to(base)``; without it the compiled compiler could not
        compute its own identity and fell back to "unknown", which the
        fail-closed archive staleness rule then read as a stale runtime.
        """
        base = str(other)
        mine = self._raw
        if os.sep == "\\":
            base = base.replace("/", "\\")
            mine = mine.replace("/", "\\")
            base_drive, base_tail = _op.splitdrive(base)
            mine_drive, mine_tail = _op.splitdrive(mine)
            if _op.normcase(base_drive) != _op.normcase(mine_drive) or base_tail.startswith("\\") != mine_tail.startswith("\\"):
                raise ValueError(repr(mine) + " is not in the subpath of " + repr(base))
            base_parts: list = []
            mine_parts: list = []
            for part in base_tail.split("\\"):
                if part and part != ".":
                    base_parts.append(part)
            for part in mine_tail.split("\\"):
                if part and part != ".":
                    mine_parts.append(part)
            if len(base_parts) > len(mine_parts):
                raise ValueError(repr(mine) + " is not in the subpath of " + repr(base))
            index = 0
            while index < len(base_parts):
                if base_parts[index].lower() != mine_parts[index].lower():
                    raise ValueError(repr(mine) + " is not in the subpath of " + repr(base))
                index = index + 1
            relative = "\\".join(mine_parts[len(base_parts):])
            return self.__class__(relative if relative else ".")
        if base == mine:
            return self.__class__(".")
        prefix = base if base.endswith(os.sep) else base + os.sep
        if not mine.startswith(prefix):
            raise ValueError(
                repr(mine) + " is not in the subpath of " + repr(base)
            )
        return self.__class__(mine[len(prefix):])

    def match(self, pattern: str) -> bool:
        if pattern.startswith("*."):
            return self.name.endswith(pattern[1:])
        return self.name == pattern or self._raw == pattern


class Path(PurePath):
    @property
    def parents(self) -> list:
        """Same ancestors as ``PurePath.parents``, as ``Path`` objects."""
        out: list = []
        for raw in self._parent_raw_paths():
            out.append(self.__class__(raw))
        return out

    def absolute(self) -> "Path":
        if os.sep == "\\":
            return Path(_op.abspath(self._raw))
        if _op.isabs(self._raw):
            return Path(self._raw)
        return Path(_op.join(os.getcwd(), self._raw))

    def resolve(self, strict: bool = False) -> "Path":
        """Absolute, normalized path.

        Symlinks are NOT followed: there is no native ``realpath`` yet, so
        this is ``absolute()`` plus ``normpath``.  Same kind of documented
        narrowing as ``is_file``/``is_dir`` above, and it is what the callers
        in this tree need -- ``Path(__file__).resolve().parents[2]`` wants an
        absolute repo root, not link identity.
        """
        if os.sep == "\\":
            if strict and not _op.exists(self._raw):
                raise FileNotFoundError(self._raw)
            return Path(_op.realpath(self._raw))
        return Path(_op.normpath(self.absolute()._raw))

    @staticmethod
    def _name_matches(name: str, pattern: str) -> bool:
        """``*`` and ``?`` matching, which is what this tree's globs use.

        Deliberately not a full ``fnmatch``: importing the provider's
        ``fnmatch`` from here fails to resolve inside the compiled closure,
        and character classes appear in no call site.  A pattern containing
        ``[`` is rejected rather than silently mismatched.
        """
        if "[" in pattern:
            raise ValueError(
                "pcc pathlib glob supports * and ? only, got " + repr(pattern)
            )
        name_len = len(name)
        pattern_len = len(pattern)
        name_index = 0
        pattern_index = 0
        star_pattern = -1
        star_name = 0
        while name_index < name_len:
            if pattern_index < pattern_len and (
                pattern[pattern_index] == "?"
                or pattern[pattern_index] == name[name_index]
            ):
                name_index = name_index + 1
                pattern_index = pattern_index + 1
            elif pattern_index < pattern_len and pattern[pattern_index] == "*":
                star_pattern = pattern_index
                star_name = name_index
                pattern_index = pattern_index + 1
            elif star_pattern >= 0:
                pattern_index = star_pattern + 1
                star_name = star_name + 1
                name_index = star_name
            else:
                return False
        while pattern_index < pattern_len and pattern[pattern_index] == "*":
            pattern_index = pattern_index + 1
        return pattern_index == pattern_len

    def _walk_entries(self, recursive: bool) -> list:
        out: list = []
        stack: list = [self._raw]
        while stack:
            current = stack.pop()
            try:
                names = sorted(os.listdir(current))
            except OSError:
                continue
            for entry in names:
                full = _op.join(current, entry)
                out.append(full)
                if recursive and _op.isdir(full):
                    stack.append(full)
        return out

    def glob(self, pattern: str) -> list:
        """Non-recursive match in this directory, sorted like a sorted walk.

        CPython returns a generator; a list is the same thing to every caller
        in this tree and keeps the provider free of generator lowering.
        """
        out: list = []
        for full in self._walk_entries(False):
            if self._name_matches(_op.basename(full), pattern):
                out.append(self.__class__(full))
        out.sort(key=lambda item: str(item))
        return out

    def rglob(self, pattern: str) -> list:
        """Recursive ``glob``; ``"*"`` therefore means every entry below."""
        out: list = []
        for full in self._walk_entries(True):
            if self._name_matches(_op.basename(full), pattern):
                out.append(self.__class__(full))
        out.sort(key=lambda item: str(item))
        return out

    def exists(self) -> bool:
        return _op.exists(self._raw)

    def is_file(self) -> bool:
        # Both of these answered ``exists()``, so a directory was a file and a
        # file was a directory.  ``bootstrap_cache_identity`` branches on
        # ``entry.is_file()`` to decide between "hash this one file" and "walk
        # this tree": compiled, it took the file branch for the ``pcc``
        # package and hashed 2 paths where the host hashed 606, so pcc1's
        # codegen identity never matched the one recorded in a runtime
        # archive and every archive read as stale.  ``os.path.isfile`` and
        # ``isdir`` are both native and agree with CPython.
        return _op.isfile(self._raw)

    def is_dir(self) -> bool:
        return _op.isdir(self._raw)

    def read_bytes(self) -> bytes:
        with open(self._raw, "rb") as f:
            return f.read()

    def read_text(
        self,
        encoding: str = "utf-8",
        errors: str = "strict",
        newline: str | None = None,
    ) -> str:
        # Match Path.read_text's universal-newline default. An explicit empty
        # string has different read semantics and must reach open unchanged.
        with open(
            self._raw, "r", encoding=encoding, errors=errors, newline=newline
        ) as f:
            return f.read()

    def write_text(
        self,
        s: str,
        encoding: str = "utf-8",
        errors: str = "strict",
        newline: str | None = None,
    ) -> int:
        with open(
            self._raw, "w", encoding=encoding, errors=errors, newline=newline
        ) as f:
            return f.write(s)
