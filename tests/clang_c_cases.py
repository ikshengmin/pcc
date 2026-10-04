from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
from pcc.driver.project import TranslationUnit
from tests.owned_c_corpus import run_owned_c_corpus
from tests.worker_process import run_worker_process


RUN_LINE_RE = re.compile(r"^\s*//\s*RUN:\s*(.*)$")
PCC_STD_COMPAT_MAP = {
    "c23": "c2x",
    "gnu23": "gnu2x",
    "c2y": "c2x",
    "gnu2y": "gnu2x",
}


@dataclass(frozen=True)
class ClangCCaseConfig:
    mode: str
    native_cflags: tuple[str, ...] = ()
    cpp_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class CStageResult:
    stage: str
    returncode: int
    stdout: str = ""
    stderr: str = ""
    completed: bool = True


@dataclass(frozen=True)
class CCaseResult:
    """Keep build results separate from an actually completed program run."""

    stages: tuple[CStageResult, ...]

    @property
    def returncode(self) -> int:
        return self.stages[-1].returncode

    @property
    def stdout(self) -> str:
        return self.stages[-1].stdout

    @property
    def stderr(self) -> str:
        return self.stages[-1].stderr

    @property
    def executed(self) -> bool:
        last = self.stages[-1]
        return last.stage == "run" and last.completed

    def require_execution(self, label: str) -> None:
        last = self.stages[-1]
        assert self.executed, (
            f"{label} did not execute: {last.stage} "
            f"returncode={last.returncode}, completed={last.completed}:\n{last.stderr}"
        )


def _stage_result(stage: str, result) -> CStageResult:
    return CStageResult(stage, result.returncode, result.stdout, result.stderr)


def subprocess_env():
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    return env


def case_config(case_path: Path) -> ClangCCaseConfig:
    run_lines = _run_lines(_read_case_source(case_path))
    mode = "runtime"
    native_cflags: list[str] = []
    cpp_args: list[str] = []
    pcc_run_line = _compiler_run_segment(run_lines[0]) if run_lines else ""

    if any(
        token in line
        for line in run_lines
        for token in ("-emit-llvm", "-emit-llvm-only", "-fsyntax-only", "-verify")
    ):
        mode = "compile_only"

    for line in run_lines:
        for token in _compiler_run_segment(line).split():
            if token.startswith("-std="):
                native_cflags.append(token)
            elif token == "-w" or token.startswith("-W"):
                native_cflags.append(token)
            elif token in {"-fblocks", "-fwritable-strings"}:
                native_cflags.append(token)

    for token in pcc_run_line.split():
        if token.startswith("-std="):
            std_value = token.split("=", 1)[1]
            if not (
                std_value.startswith("c++")
                or std_value.startswith("gnu++")
            ):
                cpp_args.append(f"-std={PCC_STD_COMPAT_MAP.get(std_value, std_value)}")
        elif token.startswith("-D") or token.startswith("-U"):
            cpp_args.append(token)

    return ClangCCaseConfig(
        mode=mode,
        native_cflags=tuple(_dedupe(native_cflags)),
        cpp_args=tuple(_dedupe(cpp_args)),
    )


def compile_native(case_path: Path, repo_root: Path) -> subprocess.CompletedProcess[str]:
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if cc is None:
        raise RuntimeError("host C compiler not found")

    config = case_config(case_path)
    with tempfile.TemporaryDirectory(prefix="clang_c_native_compile_") as tmpdir:
        output = Path(tmpdir) / ("a.o" if config.mode == "compile_only" else "a.out")
        cmd = [cc, *config.native_cflags, str(case_path)]
        if config.mode == "compile_only":
            cmd.extend(["-c", "-o", str(output)])
        else:
            cmd.extend(["-o", str(output)])
        return subprocess.run(
            cmd,
            cwd=repo_root,
            env=subprocess_env(),
            capture_output=True,
            text=True,
            timeout=20,
        )


def run_native(case_path: Path, repo_root: Path) -> CCaseResult:
    """Compile/link/run the external host reference, retaining each outcome."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if cc is None:
        raise RuntimeError("host C compiler not found")

    config = case_config(case_path)
    stages = []
    with tempfile.TemporaryDirectory(prefix="clang_c_native_") as tmpdir:
        object_path = Path(tmpdir) / "a.o"
        binary = Path(tmpdir) / "a.out"
        commands = (
            ("compile", [cc, *config.native_cflags, "-c", str(case_path), "-o", str(object_path)]),
            ("link", [cc, *config.native_cflags, str(object_path), "-o", str(binary)]),
            ("run", [str(binary)]),
        )
        for stage, command in commands:
            try:
                result = subprocess.run(
                    command, cwd=repo_root, env=subprocess_env(),
                    capture_output=True, text=True, timeout=20,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                code = 124 if isinstance(exc, subprocess.TimeoutExpired) else 1
                stages.append(CStageResult(stage, code, stderr=str(exc), completed=False))
                break
            stages.append(_stage_result(stage, result))
            if result.returncode != 0:
                break
    return CCaseResult(tuple(stages))


def compile_pcc(case_path: Path, timeout: int = 20) -> CCaseResult:
    return _run_pcc_worker("compile", case_path, timeout)


def run_pcc(case_path: Path, repo_root: Path, timeout: int = 20) -> CCaseResult:
    del repo_root
    return _run_pcc_worker("run", case_path, timeout)


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _run_lines(source: str) -> list[str]:
    lines = source.splitlines()
    collected: list[str] = []
    current = ""
    for line in lines:
        match = RUN_LINE_RE.match(line)
        if match is None:
            if current:
                collected.append(current.strip())
                current = ""
            continue
        fragment = match.group(1).rstrip()
        if fragment.endswith("\\"):
            current += fragment[:-1].strip() + " "
            continue
        current += fragment
        collected.append(current.strip())
        current = ""
    if current:
        collected.append(current.strip())
    return collected


def _compiler_run_segment(line: str) -> str:
    return line.split("|", 1)[0].strip()


def _read_case_source(case_path: Path) -> str:
    try:
        return case_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return case_path.read_text(encoding="latin-1")


def _run_pcc_worker(mode: str, case_path: Path, timeout: int) -> CCaseResult:
    result = run_worker_process(
        _pcc_worker_entry,
        (mode, str(case_path), timeout),
        timeout,
    )
    if result.timed_out:
        return CCaseResult((CStageResult("worker", 124, stderr="timeout", completed=False),))
    payload = result.payload
    if payload is None:
        return CCaseResult((CStageResult(
            "worker", 1,
            stderr=f"pcc worker exited without result (exitcode={result.exitcode})",
            completed=False,
        ),))
    return CCaseResult(tuple(CStageResult(**stage) for stage in payload["stages"]))


def _pcc_worker_entry(mode: str, case_path_str: str, timeout: int, conn) -> None:
    stages = []
    current_stage = "prepare"

    def begin_stage(stage):
        nonlocal current_stage
        if current_stage in {"compile", "link"}:
            stages.append(CStageResult(current_stage, 0))
        current_stage = stage

    try:
        case_path = Path(case_path_str)
        config = case_config(case_path)
        unit = TranslationUnit(case_path.name, str(case_path), _read_case_source(case_path))
        evaluator = CEvaluator()
        if mode == "compile":
            begin_stage("compile")
            evaluator.compile_translation_units(
                [unit],
                base_dir=str(case_path.parent),
                include_dirs=[str(case_path.parent)],
                cpp_args=config.cpp_args,
            )
            stages.append(CStageResult("compile", 0))
        else:
            result = run_owned_c_corpus(
                evaluator, [unit],
                base_dir=str(case_path.parent),
                include_dirs=[str(case_path.parent)],
                cpp_args=config.cpp_args,
                timeout=timeout,
                on_stage=begin_stage,
            )
            stages.append(_stage_result("run", result))
    except Exception as exc:
        code = 124 if isinstance(exc, subprocess.TimeoutExpired) else 1
        stages.append(CStageResult(current_stage, code, stderr=str(exc), completed=False))
    finally:
        try:
            last = stages[-1]
            conn.send({
                "returncode": last.returncode,
                "stdout": last.stdout,
                "stderr": last.stderr,
                "stages": [asdict(stage) for stage in stages],
            })
        finally:
            conn.close()
