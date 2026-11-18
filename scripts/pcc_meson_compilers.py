"""PCC compiler adapters for the explicitly selected Meson 1.9.2 host tool.

Only initial tool discovery is registered here. Stock Meson creates, compiles,
links and runs its sanity programs, evaluates the project's checks and emits
its normal backend files. The ordinary PCC compiler does not import Meson.
"""
from contextlib import contextmanager
import subprocess

from mesonbuild import build, coredata
from mesonbuild.compilers import c, cpp, detect
from mesonbuild.linkers import linkers
from mesonbuild.mesonlib import EnvironmentException
from pcc.tools.meson_cc import banner, release


class PCCLinker(linkers.PosixDynamicLinkerMixin, linkers.DynamicLinker):
    """Meson's linker interface; the qualified product is a static ET_EXEC."""
    id = "pcc-self"


class PCCArchiver(linkers.StaticLinker):
    id = "pcc-ar"

    def get_output_args(self, target):
        # Always writes an indexed, deterministic, self-contained archive.
        # Taking the full-archive option is valid when thin archives are merely
        # an optional build optimization, as with Meson's ordinary BSD ar.
        return ["--create", target]


class PCCOptions:
    id = "pcc"
    internal_libs = []
    ignore_libs = []

    def find_library(self, libname, env, extra_dirs, *args, **kwargs):
        # No implicit system archives, shared-library lookup or pretend libm.
        raise EnvironmentException(f"PCC named-library discovery is unsupported: {libname}; use an explicit archive")

    def get_dependency_gen_args(self, outtarget, outfile):
        # No rule may certify incremental Ninja rebuilds without tracking the
        # actual conditional include closure. Configuration probes do not use
        # Ninja; target graph generation remains unsupported at this boundary.
        raise EnvironmentException("PCC Meson target dependency-file generation is not implemented")

    def get_optimization_args(self, level):
        if level == "plain":
            return []
        if level in ("0", "1", "2", "3"):
            return ["-O" + level]
        raise EnvironmentException("unsupported PCC optimization level: " + level)

    def get_debug_args(self, enabled):
        if enabled:
            raise EnvironmentException("PCC Meson debug-info mode is not qualified")
        return []

    def get_warn_args(self, level):
        if level != "0":
            raise EnvironmentException("unsupported PCC warning level: " + level)
        return []

    def get_options(self):
        opts = super().get_options()
        opts[self.form_compileropt_key("std")].set_versions(["c11" if self.language == "c" else "c++17"], gnu=False)
        return opts

    def get_option_std_args(self, target, env, subproject=None):
        value = self.get_compileropt_value("std", env, target, subproject)
        return [] if value == "none" else ["-std=" + value]

    def get_compiler_check_args(self, mode):
        # In particular, do not request CPPCompiler's permissive GNU extension.
        # Unknown or invalid programs must fail their real probes.
        return []


class PCCCCompiler(PCCOptions, c.CCompiler):
    pass


class PCCCPPCompiler(PCCOptions, cpp.CPPCompiler):
    pass


def _check_tool(env, machine, tool, expected):
    command = env.lookup_binary_entry(machine, tool)
    if list(command or ()) != expected or env.is_cross_build(machine):
        raise EnvironmentException(f"PCC requires its explicit native {tool} provider")
    result = subprocess.run([*expected, "--version"], capture_output=True, text=True, timeout=10)
    if result.returncode or result.stdout != banner(tool) + "\n" or result.stderr:
        raise EnvironmentException(f"invalid PCC {tool} driver identity")


@contextmanager
def activate(commands):
    """Install discovery for one private, single-threaded Meson CLI process."""
    if coredata.version != "1.9.2":
        raise EnvironmentException("PCC adapter requires Meson 1.9.2")

    def choose_compiler(env, lang, machine):
        if lang not in ("c", "cpp"):
            raise EnvironmentException(f"PCC has no owned Meson compiler provider for {lang}")
        expected = commands[lang]
        _check_tool(env, machine, lang, expected)
        info = env.machines[machine]
        link = PCCLinker(expected, machine, None, [], system=info.system, version=release())
        kind = PCCCCompiler if lang == "c" else PCCCPPCompiler
        env.add_lang_args(lang, kind, machine)
        return kind([], expected, release(), machine, False, info, linker=link, full_version=banner(lang))

    def choose_archive(env, compiler):
        if not isinstance(compiler, (PCCCCompiler, PCCCPPCompiler)):
            raise EnvironmentException("PCC archive discovery requires an owned compiler")
        _check_tool(env, compiler.for_machine, "ar", commands["ar"])
        return PCCArchiver(commands["ar"])

    old_compiler, old_ar = detect.compiler_from_language, build.detect_static_linker
    detect.compiler_from_language, build.detect_static_linker = choose_compiler, choose_archive
    try:
        yield
    finally:
        detect.compiler_from_language, build.detect_static_linker = old_compiler, old_ar
