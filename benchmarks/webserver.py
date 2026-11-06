#!/usr/bin/env python3
"""Webserver benchmark entry: delegate HTTP policy to pcc-gateway.

Example (Mac; remaining options belong to the gateway runner)::

    uv run python benchmarks/webserver.py --output /tmp/webserver-result \
      --native /path/to/server --build-receipt /path/to/compile.json \
      --python /path/to/aiohttp/python --zrk /path/to/zrk \
      --ruby /path/to/ruby --rspec /path/to/rspec

Use already compiled artifacts. This entry does not provision a compiler or
duplicate framework/HTTP implementations. The existing core watchdog owns
the performance lock, process tree, timeout and RSS cap.
"""

import argparse
import os
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-root", type=Path, default=root.parent / "pcc-gateway")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=2400)
    parser.add_argument("--max-tree-rss-bytes", type=int, default=2147483648)
    parser.add_argument("--platform", choices=("macos", "linux-docker"),
                        default="macos" if sys.platform == "darwin" else "linux-docker")
    args, forwarded = parser.parse_known_args()
    gateway = args.gateway_root.resolve()
    module = "web_frameworks_macos" if args.platform == "macos" else "web_frameworks"
    if not (gateway / "benchmarks" / (module + ".py")).is_file():
        parser.error("gateway benchmark runner is missing: " + str(gateway))
    if args.timeout <= 0 or args.max_tree_rss_bytes <= 0:
        parser.error("timeout and RSS cap must be positive")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    guard = root / "scripts/run_process_tree_sample.py"
    command = [
        sys.executable, str(guard),
        "--result", str(output) + ".watchdog.json",
        "--samples", str(output) + ".samples.tsv",
        "--stdout", str(output) + ".stdout",
        "--stderr", str(output) + ".stderr",
        "--timeout", str(args.timeout),
        "--max-tree-rss-bytes", str(args.max_tree_rss_bytes),
        "--cwd", str(gateway), "--", sys.executable,
        "-m", "benchmarks." + module, "--output", str(output), *forwarded,
    ]
    os.execv(sys.executable, command)


if __name__ == "__main__":
    main()
