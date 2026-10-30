"""Select actionable connection metadata without recording payloads."""

import asyncio
from collections import deque


def expected_disconnect(error):
    """Recognize routine closure without constructing traceback metadata."""
    seen = set()
    current = error
    while current is not None and id(current) not in seen and len(seen) < 4:
        seen.add(id(current))
        closed = (type(current) is Exception and current.args
                  and isinstance(current.args[0], str)
                  and current.args[0].startswith('Connection closed'))
        if not (closed or isinstance(current, (
                asyncio.CancelledError, KeyboardInterrupt,
                asyncio.IncompleteReadError, EOFError,
                ConnectionResetError, BrokenPipeError))):
            return False
        current = (current.__cause__ if current.__cause__ is not None else
                   None if current.__suppress_context__ else current.__context__)
    return current is None


def failure_metadata(error):
    """Describe an exception without formatting its message, source or locals."""
    chain = []
    seen = set()
    current = error
    while current is not None and id(current) not in seen and len(chain) < 4:
        seen.add(id(current))
        frames = deque(maxlen=32)
        count = 0
        tb = current.__traceback__
        while tb is not None:
            code = tb.tb_frame.f_code
            frames.append({
                'file': code.co_filename,
                'line': tb.tb_lineno,
                'function': getattr(code, 'co_qualname', code.co_name),
            })
            count += 1
            tb = tb.tb_next
        error_number = getattr(current, 'errno', None)
        record = {
            'error_type': type(current).__name__,
            'errno': error_number if type(error_number) is int else None,
            'traceback': list(frames),
        }
        if count > len(frames):
            record['omitted_outer_frames'] = count - len(frames)
        chain.append(record)
        if current.__cause__ is not None:
            record['chained_by'] = 'cause'
            current = current.__cause__
        elif not current.__suppress_context__:
            record['chained_by'] = 'context'
            current = current.__context__
        else:
            current = None
        if current is None:
            record.pop('chained_by', None)
    result = chain[0]
    if len(chain) > 1:
        result['exception_chain'] = chain[1:]
    if current is not None:
        result['exception_chain_truncated'] = True
    result['expected_disconnect'] = expected_disconnect(error)
    return result


class LogPolicy:
    def __init__(self, mode='essential'):
        if mode not in ('essential', 'diagnostic'):
            raise ValueError('PPROXY_TRACE_MODE must be essential or diagnostic')
        self.mode = mode

    def select(self, event, fields, connection):
        cancelled = fields.get('error_type') in ('CancelledError', 'KeyboardInterrupt')
        if fields.get('direction') and (event == 'eof' or event.endswith('_error') and not cancelled):
            connection.setdefault('first_terminal', fields['direction'])
        if self.mode == 'diagnostic':
            return event, fields
        if event in ('process_start', 'process_stop', 'shutdown_requested', 'upstream_status'):
            return event, fields
        if cancelled:
            return None
        if event == 'client_handshake_error' and fields.get('expected_disconnect'):
            return None
        if event == 'eof':
            target = fields.get('target') or connection.get('target') or ('', 0)
            if (fields.get('direction') != 'upstream_to_client'
                    or connection.get('first_terminal') != 'upstream_to_client'
                    or fields.get('read_bytes') != 0
                    or connection.get('client_bytes', 0) == 0
                    or target[0] in ('echo', 'empty')):
                return None
            event = 'upstream_closed_without_response'
        elif not event.endswith('_error') and event != 'direct_fallback':
            return None
        if connection.get('issue_logged'):
            return None
        connection['issue_logged'] = True
        context = {key: connection[key] for key in ('client', 'local', 'protocol', 'target', 'upstream') if key in connection}
        return event, {**context, **fields}
