# Proxy stability tests

Tests exercise the `http+socks5` listener using real TCP/UDP sockets on temporary
loopback ports. They do not read browser data or use the live 8082/8083 listeners.
The default suite includes SOCKS5 UDP association cases; one opt-in soak case exercises another 48
concurrent HTTP/2 streams for 245 seconds.

| Area | Checks |
| --- | --- |
| Negotiation | SOCKS5 domain/IPv4/IPv6, HTTP CONNECT, fragmented headers, payload arriving with the handshake, abandoned handshakes |
| Logging | Essential mode suppresses normal traffic and expected cancellation; retains failure context, one issue per connection, and zero-response upstream closes; diagnostic mode remains available |
| Authentication | Valid and invalid credentials, rejection when no authentication method is shared |
| Connection failures | Delayed upstream connection, DNS failure, refusal, timeout, network unreachable, permission denial, SOCKS error propagation through a chain, HTTP 403/407/502 rejection |
| Data integrity | Eight simultaneous 1 MiB transfers, binary payloads, two-hop HTTP/SOCKS chains, 2 MiB transfer to a slow receiver |
| Cleanup | Either peer closing, abrupt transport abort, healthy connections surviving a peer failure, no retained writer/task registrations |
| CLI | SIGINT during idle, incomplete handshake and active transfer; process exit and coroutine cleanup |
| SOCKS5 UDP | ProxyBridge's exact UDP ASSOCIATE request, IPv4/IPv6/domain destinations, authentication, binary/empty payloads, STUN mapping stability across destinations, independent upstream replies, source-port pinning, malformed/fragmented packets, 32 simultaneous associations, control EOF/timeout/rejection, UDP API and standalone listeners |
| TLS/HTTP2 | Certificate and hostname verification, eight multiplexed streams, an injected in-flight RST_STREAM, GOAWAY with accepted streams completing, streamed responses |
| Soak | Direct, SOCKS5 and HTTP CONNECT; idle and heartbeat responses; six TLS sessions and 48 streams concurrently |

The HTTP/2 tests use an independent Node client and TLS origin. They check HTTP/2
inside the TCP tunnel used by browsers; they do not exercise the optional
`h2://` proxy protocol. The injected reset is first verified by a direct control.
GOAWAY may be repeated during shutdown, as allowed by
[RFC 9113 section 6.8](https://www.rfc-editor.org/rfc/rfc9113.html#section-6.8);
every observed frame must still have the expected error code and last stream ID.

## Fast suite

From this project directory:

```sh
env -u LC_ALL uv run --no-sync python -m pytest -c tests/pytest.ini -x -n0 -vv --tb=short tests
```

`tests/pytest.ini` isolates this suite from the parent pcc pytest configuration.
The existing development environment supplies pytest/pytest-xdist. TLS tests
also require `node` and `openssl` on PATH. They generate a temporary one-day
certificate, trust that certificate explicitly and verify the hostname. Missing
tools produce visible skips; a run with those skips does not qualify TLS/HTTP2.

## Long connections

```sh
gtimeout --signal=TERM --kill-after=5s 285s env -u LC_ALL PYTHONUNBUFFERED=1 uv run --no-sync python -m pytest -c tests/pytest.ini -x -n0 -vv -s --tb=short -m soak tests/test_http2_tunnel.py
```

`PPROXY_SOAK_SECONDS` can select 65–3600 seconds. Increase the outer watchdog
accordingly. The default 245 seconds exceeds both the 60-second handshake
deadline and the four-minute interval observed during the original diagnosis.
Progress is printed every 30 seconds. Keep output and JUnit XML for long runs:
append `--junitxml=/absolute/output/path/result.xml` and capture stdout/stderr.

Each ordinary Python scenario has a 12-second deadline. Each Node fixture has its
own deadline and is terminated by its owning test if needed. CLI tests launch
and stop their own process. All listeners and streams are closed in teardown.

## Interpreting results

Payload hashes, error replies, closure propagation and cleanup are asserted;
mere acceptance of a TCP connection is not considered success. A fixture failure
must also be checked on the direct path before attributing it to the proxy.

These are repeatable local regression tests. External DNS, remote proxy nodes
and authenticated Gmail/YouTube sessions require separate live evidence.
Updating this source does not reload already running proxy processes.

## Installed connection logging

The tracked `python -m pproxy.diagnostic` entrypoint defaults to
`PPROXY_TRACE_MODE=essential`. Set
`PPROXY_TRACE_PATH` to the JSONL log path and pass the ordinary proxy arguments.
It records process lifecycle, upstream availability transitions, failed
connections/handshakes and unexpected relay errors. Normal connections, normal
EOF, cancellation, connection resets and broken pipes are silent. Default mode
does not wrap stream reads/writes/drains/closes or collect per-chunk counters,
timestamps or snapshots. Routine disconnects are discarded before constructing
tracebacks. Errors retain destination and upstream metadata; payloads and
credentials are not logged.

Failures include their phase, exception type, file/function/line traceback and
exception cause/context chain. Messages, source snippets and local variable
values are omitted because they can contain credentials or traffic. Each
exception retains the innermost 32 frames, with at most four chained exceptions;
truncation is explicit. Startup records hash the running source files so line
numbers can be matched to the loaded version. The log rotates at 10 MiB with
three backups. An assertion wrapped as a connection-close error remains visible.

Set `PPROXY_TRACE_MODE=diagnostic` explicitly when per-chunk tracking and a full
connection trace (including normal traffic and zero-response upstream closes)
are needed. That mode adds overhead. The mode is read at process startup and
recorded in `process_start`.
`test_diagnostic_log.py` tests filtering and metadata, and starts the real CLI
on a temporary port to trigger a SOCKS authentication-version assertion and
an authentication failure, verify their source locations and absence of
credentials, then exchange data through a subsequent healthy tunnel.
It also checks normal transfers, EOF and resets on either peer produce no
connection log records, while relay assertions still reach the error handler.
