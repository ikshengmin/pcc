# Process routing in python-proxy

The macOS transparent proxy provider is part of python-proxy. It routes selected
applications' TCP and UDP to a local SOCKS5 listener; `-r` and `-ur` keep selecting
the TCP and UDP upstreams. No ProxyBridge installation or ZeroOmega dependency
is used. This is process routing, not the separate python-vpn integration.

```sh
python -m pproxy -l http+socks5://127.0.0.1:8082 \
  -r socks5://remote:8084 -ur socks5://remote:8084 \
  --process-rules macos/examples/process-rules.json
```

The process rules require one plain local SOCKS5 listener. Wildcard bind addresses
are reached through loopback. Multiple eligible listeners are rejected rather than
silently choosing one. An ephemeral `:0` listener uses its actual bound port.
The controller receives the selected listener's credentials over stdin.

Rules run in order: the first matching enabled rule wins, including wildcard
rules. Each rule supports `processes`, `hosts`, `ports`, `protocol`, `action`,
`enabled`, and an optional `name`. `protocol` is `tcp`, `udp`, or `both`;
`action` and `default` are `proxy`, `direct`, or `block`. Missing hosts and ports
mean any; missing protocol means both. Missing processes or action is an error.
Patterns are lists or semicolon-separated strings; `*` and `?` are supported.
Ports also accept integers or ranges such as `8000-8099`. Hosts accept IPv4/IPv6,
CIDR, address ranges, and hostname patterns. Unknown fields and malformed rules
fail before activation.

The process identity includes the signing identifier, executable basename and
full path, and enclosing `.app` names, paths and Bundle IDs. Thus
`Antigravity.app`, `Antigravity`, `com.google.antigravity`, `language_server`,
and `agy` are ordinary patterns, not hard-coded applications. A shell alias or
launcher that exits does not own its child's network flows: match the executable
which actually connects, or its enclosing `.app`. Name matching is routing policy,
not a code-signing security boundary.

UDP destination rules are evaluated per datagram, including multiple destinations
on one socket. Proxying uses a standard SOCKS5 UDP association, so WebRTC/STUN and
QUIC packets stay UDP and their encrypted content is not inspected. Hostname rules
can use the hostname supplied by macOS; if a flow provides only an IP, a hostname
rule cannot recover a name. The provider does not install a global DNS proxy or
guess names by reverse DNS. Use IP/CIDR rules when no name is available.

The owning python-proxy process and its listening endpoint are excluded to avoid
loops. This does not exclude all Python programs. Other processes follow the
configured rules/default. Proxy failures close selected flows without direct
fallback. Stopping process routing restores the system's ordinary behavior; it is
not a persistent kill switch. The controller stops with its parent, and a provider
heartbeat expires if the controller crashes. Flow count, UDP queues, direct UDP
destinations and handshake time are bounded.

For browser HTTP3 validation, use a browser profile without an explicit browser
HTTP/SOCKS proxy: this lets the browser emit QUIC, which the system provider then
captures. This does not require ZeroOmega. Neither TLS interception nor a root
certificate is involved. WebRTC and HTTP3 must be qualified through the installed
provider; a passing SOCKS5 wire test alone does not prove system interception.

## Build and install

Builds require Xcode. Build-only packages can be compiled without a signing
identity; they **cannot activate** the provider:

```sh
uv run python macos/build.py --unsigned --out /tmp/python-proxy-macos-build
```

A deployable package needs your Developer ID Application identity, team identifier,
and provisioning profiles granting `app-proxy-provider-systemextension` for the
app and its `.extension` identifier. The app also needs system-extension install
permission. Bundle identifiers must belong to your signing team.

```sh
uv run python macos/build.py --out /tmp/python-proxy-macos-signed \
  --bundle-id com.example.pythonproxy --identity 'Developer ID Application: ...' \
  --team-id YOURTEAMID --app-profile /path/app.provisionprofile \
  --extension-profile /path/extension.provisionprofile
```

The ZIP includes `PythonProxy.app` with the provider, the python-proxy Python
sources, example rules, and an adjacent `pproxy` launcher. Python is supplied by
your existing environment (`PPROXY_PYTHON` overrides the interpreter); optional
proxy protocol dependencies remain in that environment. Move the signed app to
`/Applications/PythonProxy.app` and use the existing python-proxy command above,
or set `PPROXY_MACOS_APP` when using the bundled launcher. macOS must approve the
system extension and network configuration. Distribution to other Macs also
requires the normal Apple notarization workflow; the build script does not submit
credentials or change system settings. Do not disable SIP to install a test build.

`--capture-app /path/PythonProxy.app` selects another signed build. The controller
offers `status` and `stop` commands at `PythonProxy.app/Contents/MacOS/PythonProxy`.
It selects only its own provider identifier and does not modify Proxifier or
ProxyBridge network configurations. Avoid overlapping rules in another active
transparent proxy when qualifying routing.

## Provenance and qualification

Adapted from InterceptSuite/ProxyBridge revision
`8cb9dceeed582fcd7c2baed8d20eec34934732fc`, especially its macOS provider and
SOCKS relay lifecycle. The MIT notice is in `third_party/ProxyBridge.LICENSE`.
The port replaces the GUI/profile/update system with python-proxy's listener
configuration, ordered process rules and an owned controller. No upstream
application identifier, provisioning profile or signing identity is reused.

The Swift rule/wire harness and Python integration checks are separate from the
installed-provider gate. Until a signed package is approved and tested on this
Mac, process interception, application coverage and browser HTTP3/WebRTC through
this new provider remain unqualified.
