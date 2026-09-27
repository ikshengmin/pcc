from __future__ import annotations

"""Pure target-triple matchers for the self backend."""


def _target_components(triple: str) -> list[str]:
    components = triple.lower().split("-")
    if len(components) < 2 or len(components) > 4:
        return []
    for component in components:
        if not component:
            return []
        for char in component:
            if char not in "abcdefghijklmnopqrstuvwxyz0123456789_.":
                return []
    return components


def _matches_versioned_os(component: str, name: str) -> bool:
    if component == name:
        return True
    if not component.startswith(name):
        return False
    version = component[len(name) :]
    for part in version.split("."):
        if not part:
            return False
        for char in part:
            if char not in "0123456789":
                return False
    return True


def is_aarch64_darwin_triple(triple: str) -> bool:
    components = _target_components(triple)
    if len(components) < 3:
        return False
    return (
        (components[0] == "arm64" or components[0] == "aarch64")
        and components[1] == "apple"
        and (
            _matches_versioned_os(components[2], "darwin")
            or _matches_versioned_os(components[2], "macosx")
        )
    )


def is_x86_64_linux_triple(triple: str) -> bool:
    components = _target_components(triple)
    if not components:
        return False
    if components[0] != "x86_64" and components[0] != "amd64":
        return False
    # Keep unambiguous compact arch-OS and arch-OS-ABI aliases. In the
    # canonical arch-vendor-OS[-ABI] form only the OS field selects Linux;
    # GNU is an ABI/environment label also used by Windows and other OSes.
    if len(components) == 2:
        return _matches_versioned_os(components[1], "linux")
    if len(components) == 3 and components[2] in ("gnu", "musl"):
        return _matches_versioned_os(components[1], "linux")
    return _matches_versioned_os(components[2], "linux")


def is_aarch64_linux_triple(triple: str) -> bool:
    parts = _target_components(triple)
    if not parts or parts[0] not in ("aarch64", "arm64"):
        return False
    return is_x86_64_linux_triple("x86_64-" + "-".join(parts[1:]))


def is_x86_64_windows_triple(triple: str) -> bool:
    parts = _target_components(triple)
    if len(parts) < 3 or parts[0] not in ("x86_64", "amd64"):
        return False
    return parts[2] in ("windows", "win32") and (
        len(parts) == 3 or parts[3] in ("msvc", "gnu")
    )


def target_os_name(triple: str) -> str:
    """Interpret the OS field without mistaking a vendor/environment for it."""
    parts = _target_components(triple)
    if not parts:
        return "unknown"
    compact_linux = len(parts) == 3 and parts[2] in ("gnu", "musl") and parts[1].startswith("linux")
    os_part = parts[1] if len(parts) == 2 or compact_linux else parts[2]
    if os_part in ("windows", "win32"):
        return "win32"
    if _matches_versioned_os(os_part, "darwin") or _matches_versioned_os(os_part, "macosx"):
        return "darwin"
    if _matches_versioned_os(os_part, "linux"):
        return "linux"
    if _matches_versioned_os(os_part, "freebsd"):
        return "freebsd"
    return "unknown"
