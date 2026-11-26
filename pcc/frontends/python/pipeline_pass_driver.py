"""Host-process orchestration for Python frontend IR pass pipelines."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from typing import Optional

from pcc.frontends.python.compiled_default_passes import is_compiled_default_tier, run_compiled_default_tier
from pcc.frontends.python.compiled_owned_passes import owns_passes, run_owned_passes
from pcc.frontends.python.pipeline_ir_split import split_python_ir_module_for_pass_shards
from pcc.frontends.python.pipeline_modes import normalize_native_backend_name
from pcc.frontends.python.pipeline_pass_config import default_python_ir_pass_transport, effective_python_ir_pass_transport_is_memory, python_ir_pass_batch_size_summary, python_ir_pass_jobs, python_ir_pass_names_allow_module_sharding, python_ir_pass_should_skip_module, python_ir_pass_skip_modules_for_batch, python_ir_pass_split_large_modules_enabled, python_ir_pass_split_shard_bytes, python_ir_pass_split_threshold_bytes, python_ir_pass_timeout_seconds, python_ir_pass_transport_is_memory, resolve_python_ir_pass_names, seconds_debug_text, small_int_decimal
from pcc.driver.paths import join_strings


def _pcc_debug_write_exc(path: str, label: str, exc: BaseException, cmd: Optional[list] = None) -> None:
    """Capture CalledProcessError/FileNotFoundError details without touching .returncode."""
    try:
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(f"label={label}\n")
            stream.write(f"exc_type={type(exc).__module__}.{type(exc).__name__}\n")
            try:
                stream.write(f"exc_args={exc.args!r}\n")
            except Exception as e:
                stream.write(f"exc_args=<unreadable: {e!r}>\n")
            try:
                stream.write(f"returncode={exc.returncode!r}\n")
            except Exception as e:
                stream.write(f"returncode=<unreadable: {type(e).__name__}: {e}>\n")
            if cmd is not None:
                try:
                    stream.write(f"cmd={cmd!r}\n")
                except Exception as e:
                    stream.write(f"cmd=<unreadable: {e!r}>\n")
    except Exception:
        pass



class PassDriverError(RuntimeError):
    """The isolated IR-pass process failed its orchestration contract."""


_SINGLE_HOST_CODE = (
    "import os\n"
    "import sys\n"
    "pcc_source_root = sys.argv[1]\n"
    "if pcc_source_root and pcc_source_root not in sys.path:\n"
    "    sys.path.insert(0, pcc_source_root)\n"
    "if pcc_source_root:\n"
    "    os.environ.setdefault('PCC_SOURCE_ROOT', pcc_source_root)\n"
    "    os.environ.setdefault('PCC_REPO_ROOT', pcc_source_root)\n"
    "from pcc.frontends.python.ir_pass_pipeline import run_python_ir_pass_pipeline\n"
    "module_name, pass_csv, ir_path, out_path, default_transport = sys.argv[2:7]\n"
    "if default_transport and not str(os.environ.get('PCC_PYTHON_IR_PASS_TRANSPORT', '') or '').strip():\n"
    "    os.environ['PCC_PYTHON_IR_PASS_TRANSPORT'] = default_transport\n"
    "pass_names = tuple(name.strip() for name in pass_csv.split(',') if name.strip())\n"
    "with open(ir_path, 'r', encoding='utf-8') as f:\n"
    "    ir_text = f.read()\n"
    "out = run_python_ir_pass_pipeline(ir_text, pass_names=pass_names, module_name=module_name)\n"
    "with open(out_path, 'w', encoding='utf-8') as f:\n"
    "    f.write(out)\n"
)


_BATCH_HOST_CODE = (
    "import multiprocessing as mp\n"
    "import os\n"
    "import sys\n"
    "pcc_source_root = sys.argv[1]\n"
    "if pcc_source_root and pcc_source_root not in sys.path:\n"
    "    sys.path.insert(0, pcc_source_root)\n"
    "if pcc_source_root:\n"
    "    os.environ.setdefault('PCC_SOURCE_ROOT', pcc_source_root)\n"
    "    os.environ.setdefault('PCC_REPO_ROOT', pcc_source_root)\n"
    "from pcc.frontends.python.ir_pass_pipeline import run_python_ir_pass_pipeline\n"
    "from pcc.frontends.python.pipeline_pass_driver import split_large_modules_for_passes\n"
    "from pcc.frontends.python.pipeline_pass_config import python_ir_pass_should_skip_module\n"
    "def _run_one(item):\n"
    "    module_name, ir_path, out_path, skip_passes = item\n"
    "    with open(ir_path, 'r', encoding='utf-8') as f:\n"
    "        ir_text = f.read()\n"
    "    if skip_passes:\n"
    "        out = ir_text\n"
    "    else:\n"
    "        out = run_python_ir_pass_pipeline(ir_text, pass_names=pass_names, module_name=module_name)\n"
    "    with open(out_path, 'w', encoding='utf-8') as f:\n"
    "        f.write(out)\n"
    "    return 0\n"
    "jobs = int(sys.argv[2])\n"
    "pass_csv = sys.argv[3]\n"
    "split_large_modules = sys.argv[4] == '1'\n"
    "result_path = sys.argv[5]\n"
    "default_transport = sys.argv[6]\n"
    "if default_transport and not str(os.environ.get('PCC_PYTHON_IR_PASS_TRANSPORT', '') or '').strip():\n"
    "    os.environ['PCC_PYTHON_IR_PASS_TRANSPORT'] = default_transport\n"
    "skip_modules = set(name for name in sys.argv[7].split(',') if name)\n"
    "def _module_should_skip_passes(module_name):\n"
    "    if module_name in skip_modules:\n"
    "        return True\n"
    "    marker = '.__pass_shard_'\n"
    "    marker_pos = module_name.find(marker)\n"
    "    base_module = module_name[:marker_pos] if marker_pos >= 0 else module_name\n"
    "    if base_module in skip_modules:\n"
    "        return True\n"
    "    if python_ir_pass_should_skip_module(module_name):\n"
    "        return True\n"
    "    if base_module != module_name and python_ir_pass_should_skip_module(base_module):\n"
    "        return True\n"
    "    return False\n"
    "items = sys.argv[8:]\n"
    "pass_names = tuple(name.strip() for name in pass_csv.split(',') if name.strip())\n"
    "if len(items) % 2 != 0:\n"
    "    raise SystemExit(2)\n"
    "input_modules = []\n"
    "i = 0\n"
    "while i < len(items):\n"
    "    module_name = items[i]\n"
    "    ir_path = items[i + 1]\n"
    "    with open(ir_path, 'r', encoding='utf-8') as f:\n"
    "        input_modules.append((module_name, f.read()))\n"
    "    i += 2\n"
    "if split_large_modules:\n"
    "    input_modules = split_large_modules_for_passes(input_modules, list(pass_names))\n"
    "result_dir = os.path.dirname(result_path)\n"
    "tasks = []\n"
    "for index, pair in enumerate(input_modules):\n"
    "    module_name, ir_text = pair\n"
    "    ir_path = os.path.join(result_dir, 'input_expanded_' + str(index) + '.ll')\n"
    "    out_path = os.path.join(result_dir, 'output_expanded_' + str(index) + '.ll')\n"
    "    with open(ir_path, 'w', encoding='utf-8') as f:\n"
    "        f.write(ir_text)\n"
    "    tasks.append((module_name, ir_path, out_path, _module_should_skip_passes(module_name)))\n"
    "if jobs <= 0:\n"
    "    jobs = os.cpu_count() or 1\n"
    "jobs = max(1, min(len(tasks), jobs))\n"
    "if jobs > 1 and len(tasks) > 1:\n"
    "    try:\n"
    "        mp.set_start_method('fork')\n"
    "    except (RuntimeError, ValueError):\n"
    "        pass\n"
    "    if mp.get_start_method(allow_none=True) == 'fork':\n"
    "        with mp.Pool(processes=jobs) as pool:\n"
    "            pool.map(_run_one, tasks)\n"
    "    else:\n"
    "        for task in tasks:\n"
    "            _run_one(task)\n"
    "else:\n"
    "    for task in tasks:\n"
    "        _run_one(task)\n"
    "with open(result_path, 'w', encoding='utf-8') as out:\n"
    "    for idx, task in enumerate(tasks):\n"
    "        module_name, _ir_path, out_path, _skip_passes = task\n"
    "        out.write(str(idx) + '\\t' + module_name + '\\t' + out_path + '\\n')\n"
)


# The owned passes of a host (CPython) build, one module per pool task. The
# coordinator used to run them module after module: 25 s of a 142 s compile
# of the handler benchmark, all on the critical path.
_OWNED_BATCH_HOST_CODE = (
    "import multiprocessing as mp\n"
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from pcc.frontends.python.compiled_owned_passes import run_owned_passes\n"
    "jobs = int(sys.argv[2])\n"
    "pass_names = [name for name in sys.argv[3].split(',') if name]\n"
    "items = sys.argv[4:]\n"
    "def _run_one(pair):\n"
    "    with open(pair[0], 'r', encoding='utf-8') as f:\n"
    "        text = f.read()\n"
    "    out = run_owned_passes(text, pass_names)\n"
    "    with open(pair[1], 'w', encoding='utf-8') as f:\n"
    "        f.write(out)\n"
    "    return 0\n"
    "pairs = [(items[i], items[i + 1]) for i in range(0, len(items), 2)]\n"
    "if jobs > 1 and len(pairs) > 1:\n"
    "    mp.set_start_method('fork', force=True)\n"
    "    with mp.Pool(processes=min(jobs, len(pairs))) as pool:\n"
    "        pool.map(_run_one, pairs, chunksize=1)\n"
    "else:\n"
    "    for pair in pairs:\n"
    "        _run_one(pair)\n"
)

# Below this much IR a fresh interpreter's start-up outweighs the pool.
OWNED_PARALLEL_MIN_BYTES = 1000000


def run_owned_passes_parallel(
    module_ir_texts: list[tuple[str, str]],
    pass_names: list[str],
    host_python_command,
    pcc_source_root,
) -> Optional[list[tuple[str, str]]]:
    """Run the owned passes of several modules in a host process pool.

    Each module's passes depend only on that module's text, so the result is
    the serial loop's, module for module. Returns None where the serial loop
    applies: inside pcc1 (no host interpreter), for fewer than two modules
    to optimize, below ``OWNED_PARALLEL_MIN_BYTES`` or with one job. A worker
    failure raises; there is no fallback.
    """
    if sys.implementation.name == "pcc":
        return None
    texts: list[str] = []
    order: list[tuple[int, int]] = []
    total_bytes = 0
    for module_name, ir_text in module_ir_texts:
        text = str(ir_text)
        texts.append(text)
        if not python_ir_pass_should_skip_module(module_name):
            # Largest first: the pool hands out one module at a time.
            order.append((-len(text), len(texts) - 1))
            total_bytes += len(text)
    if len(order) < 2 or total_bytes < OWNED_PARALLEL_MIN_BYTES:
        return None
    jobs = python_ir_pass_jobs(len(order))
    if jobs < 2:
        return None
    order.sort()
    results = list(texts)
    with tempfile.TemporaryDirectory(prefix="pcc_owned_passes_") as tmp:
        command = [host_python_command(), "-c", _OWNED_BATCH_HOST_CODE]
        command.append(pcc_source_root())
        command.append(small_int_decimal(jobs))
        command.append(join_strings(pass_names, ","))
        out_paths: list[tuple[int, str]] = []
        for _negative_size, index in order:
            stem = os.path.join(tmp, "module_" + small_int_decimal(index))
            in_path = stem + ".in.ll"
            out_path = stem + ".out.ll"
            with open(in_path, "w", encoding="utf-8") as stream:
                stream.write(texts[index])
            command.append(in_path)
            command.append(out_path)
            out_paths.append((index, out_path))
        timeout_seconds = python_ir_pass_timeout_seconds()
        try:
            if timeout_seconds is None:
                subprocess.run(command, check=True)
            else:
                subprocess.run(command, check=True, timeout=float(timeout_seconds))
        except subprocess.TimeoutExpired as exc:
            raise PassDriverError(
                "owned pass pool timed out after " + seconds_debug_text(exc.timeout)
                + "; modules=" + small_int_decimal(len(order))
            ) from exc
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            detail = f" (exit {exc.returncode})" if hasattr(exc, "returncode") else ""
            raise PassDriverError("owned pass pool failed" + detail) from exc
        for index, out_path in out_paths:
            with open(out_path, "r", encoding="utf-8") as stream:
                results[index] = stream.read()
    out: list[tuple[str, str]] = []
    position = 0
    for module_name, _ir_text in module_ir_texts:
        out.append((module_name, results[position]))
        position += 1
    return out


def split_large_modules_for_passes(
    module_ir_texts: list[tuple[str, str]],
    pass_names: list[str],
) -> list[tuple[str, str]]:
    if not python_ir_pass_transport_is_memory():
        return module_ir_texts
    if not python_ir_pass_split_large_modules_enabled():
        return module_ir_texts
    if not python_ir_pass_names_allow_module_sharding(pass_names):
        return module_ir_texts
    threshold = python_ir_pass_split_threshold_bytes()
    shard_bytes = python_ir_pass_split_shard_bytes()
    out: list[tuple[str, str]] = []
    for module_index, (module_name, ir_text) in enumerate(module_ir_texts):
        text = str(ir_text)
        if len(text) < threshold:
            out.append((module_name, text))
            continue
        shards = split_python_ir_module_for_pass_shards(
            text,
            export_prefix="__pcp" + small_int_decimal(module_index) + "_",
            shard_bytes=shard_bytes,
        )
        if len(shards) <= 1:
            out.append((module_name, text))
            continue
        for index, shard_text in enumerate(shards):
            out.append(
                (
                    module_name + ".__pass_shard_" + small_int_decimal(index),
                    shard_text,
                )
            )
    return out


def apply_passes(
    ir_text: str,
    *,
    module_name: str,
    host_python_command,
    pcc_source_root,
    logger=None,
    verbose: bool = False,
    default_raw: Optional[str] = None,
) -> str:
    pass_names = resolve_python_ir_pass_names(default_raw=default_raw)
    if not pass_names or python_ir_pass_should_skip_module(module_name):
        return str(ir_text)
    if _compiled_default_requested(pass_names, default_raw):
        if verbose and logger is not None:
            logger(
                "python IR passes["
                + module_name
                + "]: owned "
                + join_strings(pass_names, ", ")
            )
        return run_owned_passes(str(ir_text), pass_names)
    if str(default_raw or "").strip().lower() == "default":
        if not owns_passes(pass_names):
            raise PassDriverError(
                "self optimizer does not own requested passes: " + join_strings(pass_names, ", ")
            )
        return run_owned_passes(str(ir_text), pass_names)
    default_transport = default_python_ir_pass_transport(pass_names, default_raw)
    if verbose and logger is not None:
        logger(
            "python IR passes["
            + module_name
            + "]: "
            + join_strings(pass_names, ", ")
        )
    with tempfile.TemporaryDirectory(prefix="pcc_py_ir_passes_") as tmp:
        ir_path = str(os.path.join(tmp, "input.ll"))
        out_path = str(os.path.join(tmp, "output.ll"))
        with open(ir_path, "w", encoding="utf-8") as stream:
            stream.write(str(ir_text))
        cmd = [
            host_python_command(),
            "-c",
            _SINGLE_HOST_CODE,
            pcc_source_root(),
            module_name,
            join_strings(pass_names, ","),
            ir_path,
            out_path,
            str(default_transport or ""),
        ]
        timeout_seconds = python_ir_pass_timeout_seconds()
        try:
            if timeout_seconds is None:
                subprocess.run(cmd, check=True)
            else:
                subprocess.run(cmd, check=True, timeout=float(timeout_seconds))
        except subprocess.TimeoutExpired as exc:
            raise PassDriverError(
                "Python IR pass pipeline timed out for module "
                f"{module_name!r} after {seconds_debug_text(exc.timeout)}; passes="
                + join_strings(pass_names, ",")
                + " ir_bytes="
                + small_int_decimal(len(str(ir_text)))
            ) from exc
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            _pcc_debug_write_exc("/tmp/pcc_pass_driver_exc.txt", "single", exc, cmd)
            detail = f" (exit {exc.returncode})" if hasattr(exc, "returncode") else ""
            raise PassDriverError(
                "Python IR pass pipeline failed for module "
                f"{module_name!r}{detail}"
            ) from exc
        with open(out_path, "r", encoding="utf-8") as stream:
            return stream.read()


def apply_passes_many(
    module_ir_texts: list[tuple[str, str]],
    *,
    apply_one,
    host_python_command,
    pcc_source_root,
    logger=None,
    verbose: bool = False,
    default_raw: Optional[str] = None,
) -> list[tuple[str, str]]:
    pass_names = resolve_python_ir_pass_names(default_raw=default_raw)
    if not pass_names:
        return [(name, str(text)) for name, text in module_ir_texts]
    if not module_ir_texts:
        return []
    if _compiled_default_requested(pass_names, default_raw):
        parallel = run_owned_passes_parallel(
            module_ir_texts, pass_names, host_python_command, pcc_source_root,
        )
        out: list[tuple[str, str]] = []
        if parallel is not None:
            out = parallel
        else:
            for module_name, ir_text in module_ir_texts:
                text = str(ir_text)
                if python_ir_pass_should_skip_module(module_name):
                    out.append((module_name, text))
                else:
                    out.append(
                        (
                            module_name,
                            run_owned_passes(text, pass_names),
                        )
                    )
        if verbose and logger is not None:
            logger(
                "python IR passes batch["
                + str(len(out))
                + " modules]: owned "
                + join_strings(pass_names, ", ")
            )
        return out
    if str(default_raw or "").strip().lower() == "default":
        if not owns_passes(pass_names):
            raise PassDriverError(
                "self optimizer does not own requested passes: " + join_strings(pass_names, ", ")
            )
        parallel_results = run_owned_passes_parallel(
            module_ir_texts, pass_names, host_python_command, pcc_source_root,
        )
        if parallel_results is not None:
            return parallel_results
        owned_results: list[tuple[str, str]] = []
        for module_name, ir_text in module_ir_texts:
            text = str(ir_text)
            if not python_ir_pass_should_skip_module(module_name):
                text = run_owned_passes(text, pass_names)
            owned_results.append((module_name, text))
        return owned_results
    default_transport = default_python_ir_pass_transport(pass_names, default_raw)
    normalized = [(name, str(text)) for name, text in module_ir_texts]
    split_large_modules = (
        python_ir_pass_split_large_modules_enabled()
        and effective_python_ir_pass_transport_is_memory(default_transport)
        and python_ir_pass_names_allow_module_sharding(pass_names)
    )
    has_large_module = False
    if split_large_modules:
        threshold = python_ir_pass_split_threshold_bytes()
        for _name, text in normalized:
            if len(text) >= threshold:
                has_large_module = True
                break
    if len(normalized) == 1 and not has_large_module:
        name, text = normalized[0]
        return [
            (
                name,
                apply_one(
                    text,
                    module_name=name,
                    verbose=verbose,
                    default_raw=default_raw,
                ),
            )
        ]
    if verbose and logger is not None:
        logger(
            "python IR passes batch["
            + str(len(normalized))
            + " modules]: "
            + join_strings(pass_names, ", ")
        )
    with tempfile.TemporaryDirectory(prefix="pcc_py_ir_passes_many_") as tmp:
        job_count_hint = len(normalized)
        if has_large_module:
            job_count_hint = max(job_count_hint, os.cpu_count() or 1)
        result_path = str(os.path.join(tmp, "results.tsv"))
        args = [
            host_python_command(),
            "-c",
            _BATCH_HOST_CODE,
            pcc_source_root(),
            small_int_decimal(python_ir_pass_jobs(job_count_hint)),
            join_strings(pass_names, ","),
            "1" if split_large_modules else "0",
            result_path,
            str(default_transport or ""),
            join_strings(python_ir_pass_skip_modules_for_batch(normalized), ","),
        ]
        for index, (module_name, ir_text) in enumerate(normalized):
            ir_path = str(
                os.path.join(tmp, "input_" + small_int_decimal(index) + ".ll")
            )
            with open(ir_path, "w", encoding="utf-8") as stream:
                stream.write(str(ir_text))
            args.extend([module_name, ir_path])
        timeout_seconds = python_ir_pass_timeout_seconds()
        try:
            if timeout_seconds is None:
                subprocess.run(args, check=True)
            else:
                subprocess.run(args, check=True, timeout=float(timeout_seconds))
        except subprocess.TimeoutExpired as exc:
            raise PassDriverError(
                "Python IR pass batch pipeline timed out after "
                f"{seconds_debug_text(exc.timeout)}; modules={len(normalized)} passes="
                + join_strings(pass_names, ",")
                + " "
                + python_ir_pass_batch_size_summary(normalized)
            ) from exc
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            _pcc_debug_write_exc("/tmp/pcc_pass_driver_exc.txt", "batch", exc, args)
            detail = f" (exit {exc.returncode})" if hasattr(exc, "returncode") else ""
            raise PassDriverError("Python IR pass batch pipeline failed" + detail) from exc
        try:
            with open(result_path, "r", encoding="utf-8") as stream:
                result_lines = stream.read().splitlines()
        except OSError as exc:
            raise PassDriverError(
                f"Python IR pass batch pipeline failed: {exc}"
            ) from exc
        out: list[tuple[str, str]] = []
        for line in result_lines:
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            _idx_text, module_name, out_path = parts
            with open(out_path, "r", encoding="utf-8") as stream:
                out.append((module_name, stream.read()))
        if not out:
            raise PassDriverError(
                "Python IR pass batch pipeline failed: missing module result"
            )
        return out


def default_raw_for_backend(native_backend: Optional[str]) -> Optional[str]:
    if native_backend == "self":
        # The self path deliberately selects only the versioned bounded
        # default manifest.  Its finite implementation is compiled into pcc1;
        # Additional supported passes use the owned dispatcher as well.
        return "default"
    return None


def _compiled_default_requested(
    pass_names: list[str],
    default_raw: Optional[str],
) -> bool:
    # ``default_raw`` is supplied by the self-backend request path.  Do not
    # silently replace the normal LLVM/host optimizer when a caller did not
    # select that mode-labeled route.
    #
    # This predicate names the versioned bounded manifest; it no longer picks
    # the implementation.  It used to route the exact ``mem2reg,sroa`` tuple to
    # `run_compiled_default_tier`, whose mem2reg is a textual single-block
    # subset -- and because the self backend is now the default, that made the
    # weakest of pcc's three mem2reg implementations the one every self compile
    # and every runtime-archive member actually got.  Measured on the 186
    # archive modules it removed 24.5% of the memory operations where the owned
    # `native_ir.mem2reg` removes 75.1%, matching LLVM's own mem2reg+sroa to
    # within two allocas.  `run_owned_passes` is a strict superset: it runs the
    # owned kernel and then the same textual tier.
    return (
        str(default_raw or "").strip().lower() == "default"
        and is_compiled_default_tier(pass_names)
    )


def default_raw_for_request(
    native_backend: Optional[str],
    *,
    emit_llvm_only: bool,
    backend: Optional[str],
) -> Optional[str]:
    if native_backend is None and emit_llvm_only:
        requested_backend = normalize_native_backend_name(backend)
        if requested_backend == "self":
            return default_raw_for_backend("self")
    return default_raw_for_backend(native_backend)
