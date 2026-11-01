"""A small stand-in for pyperf, imported as ``pyperf`` by unchanged benchmarks.

The release statistics run each pyperformance benchmark program under
CPython and natively under pcc. Both sides import this same file, so both
time the same way: in process, after one warm-up call and a calibrated loop
count of at least MIN_TIME seconds, the median of REPEATS timings in seconds
per inner loop. pyperf's worker processes, metadata, statistics and
command-line options are not reproduced; options a benchmark adds itself are
parsed by a small parser here rather than by argparse, so that a benchmark
does not depend on pcc compiling argparse.

Each benchmark prints one line::

    @@pyperf-shim@@ <name> <seconds per loop> <loops> <inner loops> <result>

``<result>`` is an FNV-1a hash of the repr of what a ``bench_func`` function
returned on its warm-up call ("-" when there is no comparable value), so
the two sides can be checked for computing the same result.
"""

import os
import sys
import time


perf_counter = time.perf_counter

MARK = "@@pyperf-shim@@"
MIN_TIME = float(os.environ.get("PYPERF_SHIM_MIN_TIME", "0.1"))
REPEATS = int(os.environ.get("PYPERF_SHIM_REPEATS", "5"))
MAX_LOOPS = 1 << 30


def _result_hash(value):
    text = repr(value)
    if " at 0x" in text:
        # Identity-based reprs differ from run to run.
        return "-"
    digest = 0xCBF29CE484222325
    for char in text:
        digest = ((digest ^ ord(char)) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return "%016x" % digest


def _report(name, run_loops, inner_loops, result):
    run_loops(1)
    loops = 1
    while True:
        elapsed = run_loops(loops)
        if elapsed >= MIN_TIME or loops >= MAX_LOOPS:
            break
        if elapsed <= 0:
            loops = loops * 10
        else:
            scaled = int(loops * MIN_TIME * 1.2 / elapsed) + 1
            loops = min(MAX_LOOPS, max(loops * 2, scaled))
    inner = inner_loops if inner_loops else 1
    values = []
    for _ in range(REPEATS):
        values.append(run_loops(loops) / (loops * inner))
    values.sort()
    median = values[len(values) // 2]
    print(MARK, name, repr(median), loops, inner, result)
    sys.stdout.flush()
    return median


class _Namespace:
    pass


class _Option:
    def __init__(self, names, settings):
        self.names = names
        self.positional = not names[0].startswith("-")
        if "dest" in settings:
            self.dest = settings["dest"]
        elif self.positional:
            self.dest = names[0]
        else:
            longest = names[0]
            for candidate in names:
                if candidate.startswith("--"):
                    longest = candidate
                    break
            self.dest = longest.lstrip("-").replace("-", "_")
        self.action = settings.get("action", "store")
        if "default" in settings:
            self.default = settings["default"]
        elif self.action == "store_true":
            self.default = False
        elif self.action == "store_false":
            self.default = True
        elif self.action == "count":
            self.default = 0
        else:
            self.default = None
        self.convert = settings.get("type")
        self.nargs = settings.get("nargs")
        self.const = settings.get("const")
        self.choices = settings.get("choices")

    def value(self, text):
        value = text
        if self.convert is not None:
            value = self.convert(text)
        if self.choices is not None and value not in self.choices:
            raise SystemExit("pyperf shim: %r is not one of %r" % (value, self.choices))
        return value


class _ArgParser:
    def __init__(self):
        self._options = []

    def add_argument(self, *names, **settings):
        option = _Option(names, settings)
        self._options.append(option)
        return option

    def add_argument_group(self, *args, **settings):
        return self

    def add_mutually_exclusive_group(self, *args, **settings):
        return self

    def set_defaults(self, **settings):
        for option in self._options:
            if option.dest in settings:
                option.default = settings[option.dest]

    def _named(self, word):
        for option in self._options:
            if not option.positional and word in option.names:
                return option
        raise SystemExit("pyperf shim: unknown option " + word)

    def parse_args(self, argv):
        namespace = _Namespace()
        for option in self._options:
            setattr(namespace, option.dest, option.default)
        positionals = []
        for option in self._options:
            if option.positional:
                positionals.append(option)
        words = list(argv)
        index = 0
        position = 0
        while index < len(words):
            word = words[index]
            index += 1
            if word.startswith("-") and word != "-":
                value = None
                if "=" in word:
                    word, value = word.split("=", 1)
                option = self._named(word)
                if option.action == "store_true":
                    setattr(namespace, option.dest, True)
                elif option.action == "store_false":
                    setattr(namespace, option.dest, False)
                elif option.action == "store_const":
                    setattr(namespace, option.dest, option.const)
                elif option.action == "count":
                    setattr(namespace, option.dest, getattr(namespace, option.dest) + 1)
                else:
                    if value is None:
                        value = words[index]
                        index += 1
                    converted = option.value(value)
                    if option.action == "append":
                        current = getattr(namespace, option.dest)
                        current = list(current) if current else []
                        current.append(converted)
                        converted = current
                    setattr(namespace, option.dest, converted)
                continue
            if position >= len(positionals):
                raise SystemExit("pyperf shim: unexpected argument " + word)
            option = positionals[position]
            if option.nargs in ("*", "+"):
                values = [option.value(word)]
                while index < len(words) and not words[index].startswith("-"):
                    values.append(option.value(words[index]))
                    index += 1
                setattr(namespace, option.dest, values)
            else:
                setattr(namespace, option.dest, option.value(word))
            position += 1
        return namespace


class Runner:
    def __init__(self, *args, **settings):
        metadata = settings.get("metadata")
        self.metadata = dict(metadata) if metadata else {}
        self.argparser = _ArgParser()
        self.args = None

    def parse_args(self, args=None):
        if self.args is None:
            self.args = self.argparser.parse_args(sys.argv[1:] if args is None else args)
        return self.args

    def bench_time_func(self, name, time_func, *args, **settings):
        self.parse_args()

        def run_loops(loops):
            return time_func(loops, *args)

        return _report(name, run_loops, settings.get("inner_loops"), "-")

    def bench_func(self, name, func, *args, **settings):
        self.parse_args()
        result = _result_hash(func(*args))

        def run_loops(loops):
            started = perf_counter()
            for _ in range(loops):
                func(*args)
            return perf_counter() - started

        return _report(name, run_loops, settings.get("inner_loops"), result)

    def bench_async_func(self, name, func, *args, **settings):
        import asyncio

        self.parse_args()

        async def timed(loops):
            started = perf_counter()
            for _ in range(loops):
                await func(*args)
            return perf_counter() - started

        def run_loops(loops):
            return asyncio.run(timed(loops))

        return _report(name, run_loops, settings.get("inner_loops"), "-")

    def bench_command(self, name, command):
        raise SystemExit("pyperf shim: bench_command measures process startup; not applicable")

    def timeit(self, name, *args, **settings):
        raise SystemExit("pyperf shim: timeit compiles statement strings at run time; not supported")
