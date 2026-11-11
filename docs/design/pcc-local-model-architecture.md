# Local-model mode: architecture for Harness, Strata compatibility and the device layer

Status: proposed architecture, 2026-11-12. Layer 0, the Kernel IR packet path
of M1 and the public annotated-kernel CLI of M1a execute on a real device with
host pcc0. Layers 2–6 remain specifications, not implemented local-model modes.
Maintainer direction of 2026-11-12, translated: Metal is the first GPU device,
and the architecture for a local-model mode that supports Strata and Harness
must be built.

Background and evidence identities are in
[the GPU capability exploration](../research/gpu-capability-mojo-strata-exploration.md).
Claim levels and the Kernel IR contract stay in
[pcc-gpu-next-work.md](pcc-gpu-next-work.md) and
[pcc-kernel-ir.md](pcc-kernel-ir.md).

## Goal

A Harness built by pcc1 can run its agent turns on a model that executes on the
local machine, with pcc and pcc-gui as the owners underneath. "Supports Strata"
is not one thing; it is five separable seams, listed in section 4, and only the
first is needed for a first working mode.

## 1. Layers

```text
L6  product surface      pcc-gui views: model choice, download, engine status
L5  serving surface      optional loopback OpenAI/Anthropic endpoints (pcc-gateway)
L4  inference interface  Harness ModelProvider: explicit route, streaming, cancel
L3  model runtime        tokenizer, layer program, KV state, sampling
L2  tensors and weights  device buffers, no-copy weight pages, fences
L1  kernels              device code: Kernel IR output or labeled foreign source
L0  device layer         open / buffer / pipeline / dispatch behind a C ABI
```

A layer may only call the layer directly below it and the compiler intrinsics.
Layers 0 to 3 are freestanding pcc-Python or pcc-compiled Python with no
Python-object type in any device-facing signature. Layers 4 to 6 are ordinary
pcc-compiled Python.

### L0 device layer (exists)

`pcc/gpu_runtime/metal_device.py` is the first device. Its C ABI is the
device contract every later device must offer in the same shape:

```text
open(context_out)                                   close(context)
buffer_create / buffer_contents / buffer_length / buffer_release
pipeline_create(source, function, pipeline_out, diagnostic, capacity)
pipeline_limits(pipeline, limits_out)               pipeline_release
dispatch_sync(pipeline, bindings, binding_count, grid, threads)
```

Rules that are part of the contract, not of the Metal implementation:

- Every call returns a status; an unavailable device is an in-band status,
  never a silent success and never a fallback to another device or the CPU.
- A launch is validated completely before anything is encoded. A malformed
  packet or a threadgroup wider than the pipeline allows is refused.
- The caller owns buffers and pipelines; the device layer keeps no table.
- No signature carries a managed object. Buffers are raw device handles.
- The device-code dialect is an explicit input and is echoed in the result.
  A default chosen by the platform from properties of the executable is not
  acceptable: the same kernel must compile the same way from any launcher.

A whole launch can also travel as one self-describing packet
(`launch_packet`, `packet_main`). `pcc/gpu_runtime/metal_host.py` encodes it
and runs the launcher as a child process; `pcc/kernel_ir/metal_device_launch.py`
encodes a validated Kernel IR launch plan. The packet is the serialized form of
the packed-argument contract and the unit a result manifest hashes.
The annotated-kernel CLI instead calls `pcc_metal_device_launch_packet` directly
inside its native executable; it does not need that child-process adapter.
The owned host runtime still calls Apple's `newLibraryWithSource:options:error:`:
`device_owner=pcc-metal-device` names the host adapter, not ownership of the
system's shader compiler or GPU machine-code generation.

Missing at L0 and required by the layers above: asynchronous launch with a
completion fence, no-copy buffers over existing pages, a queue per stream, and
device identity fields (family, memory limits) for the fit decision.

### L1 kernels

Device code has two legal sources, and a result must say which one ran:

- `kernel_source=pcc-kernel-ir`: Metal source emitted by
  `pcc/kernel_ir/metal_finalize.py` from validated Kernel IR.
- `kernel_source=foreign-msl-asset`: Metal source written elsewhere, vendored
  with its license notice and content hash.

Either way a kernel is admitted only as a record: source hash, entry function,
binding signature (index, kind, element type, access), thread and grid policy,
and a CPU oracle. No CPU oracle means no correctness claim, as today.

#### Public annotated-kernel entry (M1a)

`@gpu.kernel` / `gpu.launch` is a bounded Python frontend to Kernel IR, not
whole-program GPU execution. `pcc/kernel_ir/entry.py` extracts kernels and
rewrites launch calls into `gpu_host_launch.py` helpers. The normal Python
pipeline compiles those helpers and links the owned `metal_device.o`; MSL is
embedded in the executable, while `.gpu/*.metal` remains inspectable evidence.
No `.air`, `.metallib`, Objective-C bridge or external host compiler is used
on this route. The CLI's inspection sidecars may be removed before execution.

The current pointer surface accepts `bytes` and `bytearray` containing f32
values; writes require `bytearray`. A synchronous launch copies buffers into
and out of Metal storage. The same buffer may be passed to multiple read-only
bindings, but any writable alias is rejected: this packet does not yet contain
an alias-preserving buffer table. These are not no-copy tensor handles.

`tests/gpu_hardware/test_gpu_kernel_launch.py` executes five gates: public-CLI
SAXPY/readback against a CPU oracle, writable-alias rejection, invalid arguments
and read-only aliases, real compile/link with subprocess creation denied, and
native f32 packing against `ctypes.c_float`. Each binary also verifies its
actual GC0–4 identity. This is host-pcc0/self evidence, not M2 lifetime or pcc1
qualification. See [current status](../status.md) for the source identities,
selected-directory results and explicitly unrun/blocked checks.

### L2 tensors and weights

- A tensor is a device buffer handle plus shape, element type and byte range.
  It is the existing `PccBufferHandle` idea with a real device handle behind
  it.
- Weights are file-backed pages wrapped as no-copy device buffers. The wrap
  needs page-aligned ranges, chunking above the device's maximum buffer
  length, and a residency decision per chunk.
- Device-visible memory is never inside a managed heap. A relocating collector
  must not be able to move or scan it; this is already the Kernel IR boundary
  rule and it extends to weights.
- Release happens on fence completion, not when a wrapper is collected. The
  external-resource seam in `pcc/gpu_gc/` is the intended owner of that edge.

### L3 model runtime

One engine interface, two owners:

```text
engine_open(model_path, options) -> engine | status
engine_limits(engine) -> context window, output budget, slots
engine_tokenize(engine, text) -> tokens
engine_prefill(engine, tokens) -> status
engine_decode(engine, sampling) -> token | end | status
engine_cancel(engine)          engine_close(engine)
```

- `engine_owner=external-process`: the engine is another program reached over
  loopback. pcc owns nothing below L4.
- `engine_owner=pcc-inprocess` or `pcc-engine-process`: a layer program that
  issues L1 kernels through L0. The first target is one small dense model that
  is token-exact against a CPU oracle. A model family with routed experts,
  SSD streaming or draft-and-verify decoding is a later, separate target.

The engine reports real limits. A route may not declare a context window or
output budget larger than the engine reports.

### L4 inference interface

Harness already has the seam: a provider registered by name whose
`complete(request)` streams chunks through the request and honors its
cancellation. A local route is one more provider, with these rules:

- The route is explicit. A local route never resolves to a cloud model, and a
  missing or stopped engine is a typed `model-unavailable` error.
- Context window and output budget come from the engine. The agent's system
  prompt and tool definitions are counted against the window before a turn is
  sent.
- The reasoning switch travels in the form the engine reads.
- Cancellation stops generation; it does not kill a shared engine.

### L5 serving surface (optional)

Loopback OpenAI- and Anthropic-compatible endpoints hosted with pcc-gateway,
so other tools can use a pcc-hosted engine. Harness itself does not need it.
It binds to `127.0.0.1` by default and requires a key when it listens beyond.

### L6 product surface

pcc-gui views for model choice by available memory, download and preparation
progress, engine status and speed. Approval modes and tool cards are Harness
behavior and are not specific to local models.

## 2. Process topology

```text
A  Harness  --loopback HTTP-->  external engine (for example Strata)
B  Harness  [ agent | provider | engine thread | device layer ]   one process
C  Harness  --loopback HTTP-->  engine process built by pcc1
```

A and C use the same client. That makes the OpenAI-compatible loopback
protocol the stable seam: the engine behind it can change from another
vendor's binary to a pcc-built process without changing Harness.

C is the intended home for a large model. A device fault or memory pressure
then ends the engine process, not the user's session, and the engine's
unified-memory footprint is separate from the application's managed heap. B is
appropriate for small models and for tests.

In B and C the device wait must not stall the runtime. A thread blocked in a
device wait must be visible to the collectors as blocked, and a virtual-thread
carrier must not be the thread that waits. The synchronous `dispatch_sync` is
therefore a building block for a dedicated engine thread, not an API for
application code on a carrier.

## 3. Ownership labels

Every result manifest for a local-model turn records, and a mismatch between
requested and actual value is an error:

```text
engine_owner      external-process | pcc-inprocess | pcc-engine-process
device_owner      none | pcc-metal-device | <provider>
kernel_source     n/a | pcc-kernel-ir | foreign-msl-asset
host_backend      self
python_libpython  off
fallback_used     false
claim level and gate result
```

`device_owner=pcc-metal-device` names the freestanding device runtime. It is
distinct from the older `pcc-metal` path whose launcher is an Objective-C
bridge, and evidence for one is not evidence for the other.

## 4. What "supports Strata" means

| Seam | Meaning | Layers touched | Needed for a first mode |
|---|---|---|---|
| S1 engine | Use a running Strata server as the engine | L4 | yes |
| S2 server | Offer the same loopback API to other tools | L5 | no |
| S3 packs | Read the same model files | L2, L3 | no |
| S4 kernels | Run its Metal kernels as foreign assets | L0, L1 | no |
| S5 engine design | Expert cache, SSD streaming, drafting | L2, L3 | no |

S1 is a route profile and a lifecycle decision. The profile mirrors the one
Strata ships for the upstream Harness: an OpenAI-completions endpoint on
loopback, the engine's real context window, an output budget that fits after
the agent's own prompt, and the engine's thinking format. The lifecycle
question is whether Harness starts and stops the engine or only connects to
one the user runs; the first mode should only connect.

S4 depends only on L0 and L1. Strata compiles Metal source at run time, which
is the operation `pipeline_create` performs, so its kernels need no
conversion; they need binding records, CPU oracles and license notices.

S3 and S5 are engine work. They are listed so that nobody reads S1 as engine
support.

## 5. Milestones and exit evidence

A milestone is done only with its evidence. Lower evidence never stands in for
higher.

| | Milestone | Exit evidence |
|---|---|---|
| M0 | Owned Metal device runtime, synchronous launch | `tests/gpu_hardware/test_metal_device_runtime.py` under `PCC_GPU_HARDWARE_STRICT=1`: real launches equal a CPU oracle, including the Kernel IR copy source. Host-pcc0, local Apple silicon. Done 2026-11-12. |
| M1 | Launch packet and runner; Kernel IR kernels through the owned device | `tests/gpu_hardware/test_metal_device_packet.py` under `PCC_GPU_HARDWARE_STRICT=1`: the copy kernel, the imported scalar GEMM and two simdgroup GEMM shapes run from their validated launch plans with `device_owner=pcc-metal-device` and equal the CPU reference. Host-pcc0. Done 2026-11-12 for those kernels; the other Kernel IR shapes are not yet run on this path. |
| M1a | Public `@gpu.kernel` / `gpu.launch` through the owned device | `tests/gpu_hardware/test_gpu_kernel_launch.py`: five executed gates, CPU/binary32 references and observed GC0–4 identities; embedded-source binaries run without inspection sidecars. Host-pcc0/self, local Darwin arm64, 2026-11-12. No external host compiler with a verified prebuilt runtime. Not pcc1 or asynchronous lifetime evidence. |
| M2 | pcc1-native and five-GC handle lifetime on the owned device | The workload from a pcc1-built program plus resource-handle lifetime checks under observed GC0–4; merely selecting a collector or running synchronous copies is insufficient. |
| M3 | Harness local route to an external engine (S1) | One prompt and one tool call through the pcc1-built Harness against a running local server; stopped engine gives `model-unavailable`; no cloud request is made. |
| M4 | Asynchronous fence, no-copy weights, foreign kernel assets (S4) | A fence-ordered release test; one foreign kernel equal to its CPU oracle. |
| M5 | One small model in an engine process (topology C) | Token-exact against a CPU oracle; Harness uses it through the M3 client unchanged. |
| M6 | A Strata-class model family (S3, S5) | Separate plan. |

M3 does not depend on M1 or M2. It can start as soon as the Harness
translation reaches a working HTTP client.

## 6. Open points

- Whether the operating system's Metal compiler is an accepted platform
  boundary under the pcc1 ownership contract is still the maintainer's
  decision. M0 relies on it.
- The asynchronous completion path needs either an Objective-C block built in
  pcc-Python or a dedicated waiting thread. Neither is designed.
- The mechanism that marks a thread as blocked during a device wait is not
  identified in the threaded runtime yet.
- pcc-gui's window and render bridge is still Objective-C built by an external
  compiler. Replacing it needs runtime class creation for delegates, which the
  device runtime does not exercise.
- No pcc1 built from current source was available when M0 was executed, so
  M0 has no pcc1 evidence of any kind.
