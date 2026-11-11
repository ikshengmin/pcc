"""Smoke program of scripts/pcc_perf.py: BENCH_N JSON payloads, no threads.

Prints the total encoded length (2666 for BENCH_N=31); a build is accepted
only when every collector prints it.
"""
import os
import json
N = int(os.environ.get('BENCH_N', '10000'))

def fetch_profile(delay_ms: int):
    return {'name': 'Ada', 'plan': 'pro'}

def fetch_notifications(delay_ms: int):
    return ['Your report is ready']

def request() -> int:
    data = {'profile': fetch_profile(0), 'notifications': fetch_notifications(0)}
    body = json.dumps(data, sort_keys=True).encode()
    expected = b'{"notifications": ["Your report is ready"], "profile": {"name": "Ada", "plan": "pro"}}'
    if body != expected:
        raise RuntimeError('benchmark response mismatch: ' + body.decode())
    return len(body)

def main():
    total = 0
    index = 0
    while index < N:
        total += request()
        index += 1
    print(total)
main()
