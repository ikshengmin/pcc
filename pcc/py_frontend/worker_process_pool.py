"""Compiler workers share an argv/environment protocol across execution owners."""

import os
import shlex
import subprocess
import sys
import time

from pcc.extern import extern, c_int64, c_ptr


_native_pool = extern("pcc_worker_process_pool", (c_ptr, c_int64), c_int64)
_native_weighted_pool = extern(
    "pcc_weighted_worker_process_pool",
    (c_ptr, c_ptr, c_int64, c_int64), c_int64,
)


def _command_spec(command):
    argv = shlex.split(command)
    env = dict(os.environ)
    while argv:
        key, separator, value = argv[0].partition("=")
        if not separator or not key or not (key[0].isalpha() or key[0] == "_"):
            break
        if not all(ch.isalnum() or ch == "_" for ch in key):
            break
        env[key] = value
        argv.pop(0)
    if not argv:
        raise ValueError("worker command has no executable")
    return argv, [key + "=" + value for key, value in env.items()]


def _host_pool(specs, width):
    """CPython's standard process API implements the host platform boundary."""
    active = []
    next_index = 0

    def signal_group(process, number):
        process.poll()
        try:
            os.killpg(process.pid, number)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Darwin reports EPERM for an orphan group containing only
            # zombies. Its leader has already been reaped in this case.
            if process.returncode is None:
                raise

    try:
        while next_index < len(specs) or active:
            pending = []
            for index, process in active:
                result = process.poll()
                if result is None:
                    pending.append((index, process))
                else:
                    signal_group(process, 9)
                    if result:
                        return ((index + 1) << 32) | (result & 4294967295)
            active = pending
            while next_index < len(specs) and len(active) < width:
                argv, env_vector = specs[next_index]
                env = dict(item.split("=", 1) for item in env_vector)
                try:
                    process = subprocess.Popen(argv, env=env, process_group=0)
                except OSError:
                    return ((next_index + 1) << 32) | 127
                active.append((next_index, process))
                next_index += 1
            if active:
                time.sleep(0.01)
    finally:
        if active:
            for _index, process in active:
                signal_group(process, 15)
            time.sleep(0.2)
            for _index, process in active:
                signal_group(process, 9)
                process.wait(timeout=2)
    return 0


def run_worker_processes(commands, width):
    specs = [_command_spec(command) for command in commands]
    if sys.implementation.name == "pcc":
        result = _native_pool(specs, width)
    else:
        result = _host_pool(specs, width)
    if result:
        index = (result >> 32) - 1
        code = result & 4294967295
        if code >= 2147483648:
            code -= 4294967296
        raise subprocess.CalledProcessError(code, commands[index])


def _host_weighted_pool(specs, reservations, width, budget):
    active = []
    pending = list(range(len(specs)))
    available = budget

    def signal_group(process, number):
        process.poll()
        try:
            os.killpg(process.pid, number)
        except ProcessLookupError:
            pass
        except PermissionError:
            if process.returncode is None:
                raise

    try:
        while pending or active:
            survivors = []
            for index, process in active:
                result = process.poll()
                if result is None:
                    survivors.append((index, process))
                    continue
                signal_group(process, 9)
                available += reservations[index]
                if result:
                    return ((index + 1) << 32) | (result & 4294967295)
            active = survivors
            while pending and len(active) < width:
                selected = next(
                    (index for index in pending
                     if reservations[index] <= available or not active),
                    None,
                )
                if selected is None:
                    break
                pending.remove(selected)
                argv, env_vector = specs[selected]
                env = dict(item.split("=", 1) for item in env_vector)
                try:
                    process = subprocess.Popen(argv, env=env, process_group=0)
                except OSError:
                    return ((selected + 1) << 32) | 127
                active.append((selected, process))
                available -= reservations[selected]
            if active:
                time.sleep(0.01)
    finally:
        for _index, process in active:
            signal_group(process, 15)
        if active:
            time.sleep(0.2)
        for _index, process in active:
            signal_group(process, 9)
            process.wait(timeout=2)
    return 0


def run_weighted_worker_processes(commands, reservations, width, budget):
    if len(commands) != len(reservations):
        raise ValueError("worker command/reservation inventory mismatch")
    if budget <= 0 or any(weight <= 0 for weight in reservations):
        raise ValueError("weighted worker reservations require positive bytes")
    specs = [_command_spec(command) for command in commands]
    if sys.implementation.name == "pcc":
        result = _native_weighted_pool(specs, reservations, width, budget)
    else:
        result = _host_weighted_pool(specs, reservations, width, budget)
    if result:
        index = (result >> 32) - 1
        code = result & 4294967295
        if code >= 2147483648:
            code -= 4294967296
        raise subprocess.CalledProcessError(code, commands[index])
