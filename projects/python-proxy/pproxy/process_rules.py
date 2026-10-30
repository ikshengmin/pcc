"""Validate and normalize ordered macOS process-routing rules."""

import csv
import ipaddress
import json
from pathlib import Path

MAX_RULE_BYTES = 256 * 1024
ACTIONS = {'proxy', 'direct', 'block'}


def patterns(value, field):
    if isinstance(value, str):
        value = next(csv.reader([value], delimiter=';', skipinitialspace=True))
    if not isinstance(value, list) or not value or len(value) > 256:
        raise ValueError('%s must be a nonempty list (or semicolon-separated string)' % field)
    result = []
    for item in value:
        if field == 'ports' and type(item) is int:
            item = str(item)
        if not isinstance(item, str) or not item.strip() or len(item.encode()) > 1024:
            raise ValueError('%s contains an empty or invalid pattern' % field)
        item = item.strip()
        if any(ord(c) < 32 for c in item):
            raise ValueError('%s contains control characters' % field)
        if field == 'ports' and item != '*':
            parts = item.split('-')
            if not 1 <= len(parts) <= 2 or not all(p.isascii() and p.isdigit() for p in parts):
                raise ValueError('Invalid port or port range: %s' % item)
            numbers = list(map(int, parts))
            if not all(1 <= p <= 65535 for p in numbers) or numbers[0] > numbers[-1]:
                raise ValueError('Invalid port or port range: %s' % item)
        if field == 'hosts' and '/' in item:
            ipaddress.ip_network(item, strict=False)
        result.append(item)
    return result


def normalize(value):
    if not isinstance(value, dict) or set(value) - {'rules', 'default'}:
        raise ValueError('Process rules must contain only rules and default')
    fallback = value.get('default', 'direct')
    if not isinstance(fallback, str) or fallback not in ACTIONS:
        raise ValueError('default must be proxy, direct, or block')
    entries = value.get('rules')
    if not isinstance(entries, list) or len(entries) > 1024:
        raise ValueError('rules must be a list with at most 1024 entries')
    rules = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) - {'name', 'processes', 'hosts', 'ports', 'protocol', 'action', 'enabled'}:
            raise ValueError('Unknown fields in rule %d' % (index + 1))
        action = entry.get('action')
        protocol = entry.get('protocol', 'both')
        enabled = entry.get('enabled', True)
        if (not isinstance(action, str) or action not in ACTIONS or
                not isinstance(protocol, str) or protocol not in {'tcp', 'udp', 'both'} or type(enabled) is not bool):
            raise ValueError('Invalid action, protocol, or enabled in rule %d' % (index + 1))
        rule = dict(processes=patterns(entry.get('processes'), 'processes'),
                    hosts=patterns(entry.get('hosts', ['*']), 'hosts'),
                    ports=patterns(entry.get('ports', ['*']), 'ports'),
                    protocol=protocol, action=action, enabled=enabled)
        if 'name' in entry:
            if not isinstance(entry['name'], str) or len(entry['name']) > 256:
                raise ValueError('Invalid rule name')
            rule['name'] = entry['name']
        rules.append(rule)
    return {'rules': rules, 'default': fallback}


def load(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_RULE_BYTES + 1)
    if len(data) > MAX_RULE_BYTES:
        raise ValueError('Process rules exceed 256 KiB')
    return normalize(json.loads(data))
