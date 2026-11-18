"""Host-process support for development rustc reference observations (not PCC admission)."""
import subprocess


_RUSTC_BASE_ENV = ('PATH', 'HOME', 'RUSTUP_HOME', 'CARGO_HOME', 'RUSTUP_TOOLCHAIN')
_RUSTC_WINDOWS_ENV = (
    # Windows loader, Rust home/temp and Visual Studio discovery locations.
    'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'PROGRAMFILES', 'PROGRAMFILES(X86)',
    'PROGRAMW6432', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE',
    'APPDATA', 'LOCALAPPDATA',
    # Preserve an already selected MSVC/SDK toolchain, not compiler flags.
    'VSINSTALLDIR', 'VCINSTALLDIR', 'VCTOOLSINSTALLDIR', 'VCTOOLSVERSION',
    'VISUALSTUDIOVERSION', 'VSCMD_ARG_HOST_ARCH', 'VSCMD_ARG_TGT_ARCH',
    'WINDOWSSDKDIR', 'WINDOWSSDKVERSION',
    'WINDOWSSDKLIBVERSION', 'UNIVERSALCRTSDKDIR', 'UCRTVERSION',
    'INCLUDE', 'LIB', 'LIBPATH',
)


def rustc_toolchain_environment(environ, *, windows, include_sdk=True):
    """Retain toolchain/OS locators without leaking arbitrary macro inputs."""
    if windows:
        # Windows environment names are case-insensitive. Retain the original
        # spelling and values rather than inventing SDK paths or PATH ordering.
        windows_keys = _RUSTC_WINDOWS_ENV if include_sdk else ('SYSTEMROOT', 'WINDIR', 'TMP', 'TEMP')
        allowed = set(_RUSTC_BASE_ENV + windows_keys)
        return {key: value for key, value in environ.items() if key.upper() in allowed}
    return {key: environ[key] for key in _RUSTC_BASE_ENV if key in environ}


def run_reference(stage, *args, **kwargs):
    """Retain captured diagnostics without changing subprocess failure semantics."""
    try:
        return subprocess.run(*args, **kwargs)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        error.add_note(
            stage + " subprocess diagnostics:\n"
            + "returncode=" + repr(getattr(error, "returncode", None)) + "\n"
            + "timeout=" + repr(getattr(error, "timeout", None)) + "\n"
            + "stdout=" + repr(error.stdout) + "\n"
            + "stderr=" + repr(error.stderr)
        )
        raise

