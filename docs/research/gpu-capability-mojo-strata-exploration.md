# GPU capability exploration: Mojo's route, pcc's current state, and a local-model mode for Harness

Status: historical baseline exploration plus a subsequent owned-route update,
2026-11-12. The original exploration was documentation-only at `7545dce5a`;
its source readings and proposed probes are preserved below, not renewed
execution evidence. Section 2.5 records the later implementation and real-device
checks. The original "today/current" comparisons in sections 2.1–2.4 and 3
refer to the baseline, not the current annotated-kernel CLI.

This document does not reorder priorities. `docs/project-intent.md` places
accelerator execution as breadth that must not displace the self-host, five-GC,
value-model and runtime-efficiency spine, and `AGENTS.md` names the source
frontends and the GC program as the active work.

## Questions

1. Can pcc obtain the GPU capability that Mojo has?
2. What GPU support does pcc have today?
3. Once the TAG translation of Harness is complete, what would a
   "run a local model" mode look like if it borrows from Strata while pcc and
   pcc-gui remain the owners underneath?

## Short answers

1. **Partly, and by a different route.** The capability *shape* (kernels
   written in the host language, compiled for a device, launched from the same
   program with proven results) is reachable on Apple silicon; pcc already has
   that skeleton. Mojo's *route* is not available to pcc: every Mojo GPU target
   is produced through MLIR and LLVM, which the pcc1 ownership contract
   excludes. Vendor breadth beyond Metal would need pcc-owned emitters for a
   documented device format, not an LLVM backend.
2. **A narrow, real Metal path with honest claim levels.** pcc lowers a finite
   kernel subset to Metal Shading Language text. The later owned runtime and
   public annotated-kernel CLI execute on-device without the clang-built
   Objective-C launcher (section 2.5); legacy reference paths still use it.
   Apple's system compiler still owns GPU machine-code generation. Only Metal
   is modeled, and no model runtime exists.
3. **Start at the protocol boundary, move ownership inward in stages.** Strata
   already runs the same upstream Harness against a local engine through an
   OpenAI-compatible loopback route. The first local-model mode for the pcc
   Harness needs no GPU work in pcc at all. Later stages move the launcher,
   then the kernels, then a model runtime into pcc, each with its own label.

## Evidence read

| Source | Revision | What was read |
|---|---|---|
| `allstoalls/pcc` | `7545dce5a` (worktree had unrelated runtime edits) | `pcc/gpu.py`, `pcc/backend/{metal,gpu_dispatch}.py`, `pcc/backend/gpu_metal_runtime.m`, `pcc/kernel_ir/`, `pcc/gpu_gc/`, `pcc/runtime/py/freestanding_metal_runtime.py`, the Metal branch of `pcc/frontends/python/pipeline.py`, `tests/gpu_hardware/`, `docs/design/pcc-gpu-*.md`, `docs/design/pcc-kernel-ir.md` |
| `allstoalls/pcc-gui` | `16c6ecf` | `README.md`, `pcc_gui/native/` |
| Harness port (sibling project, no Git revision) | read 2026-11-12 | `README.md`, `ARCHITECTURE.md`, `AGENTS.md`, `harness/model_runtime.py`, `migration/upstream.json` |
| TAG compiler | `b1a50b65` | `README.md`, Harness entries of `docs/t2-execution.md` |
| `modular/modular` | `c0bfec57fa8a` (2026-11-11) | `Mojo/lib/Compiler/ObjectCompiler/Target/`, `Target/`, `SDK/include/HAL/`, `MLRT/lib/Driver/`, `max/mojo/max/gpu/`, `max/mojo/max/_plugin/`, `max/mojo/max/_driver/max_license/README.md`, `Mojo/docs/compiler/manual/PostParserDebugging.md`, `max/docs/site/gpu/hardware-compatibility.mdx` |
| `Niko1221/Strata` | `c1ff07f1a181` (2026-11-11), app 0.2.13, engine 0.1.59 | `README.md`, `AGENTS.md`, `docs/HOW_IT_WORKS.md`, `docs/MAC.md`, `metal/CMakeLists.txt`, `metal/src/mtl_rt.h`, `apps/host/server/agent/README.md`, `integrations/harness/profile/`, repository tree and language statistics |

Not done: no pcc GPU gate was run, no Metal device was used, no Strata or MAX
binary was installed or run. Performance figures quoted from Strata are its own
published measurements. The pcc GPU design documents are dated 2026-07-06 and
2026-07-16; their "proven" statements describe those recorded runs, not the
current worktree.

## 1. What Mojo's GPU capability is made of

Mojo's GPU support is five separable layers. Paths are in `modular/modular`.

| Layer | Implementation | Notes |
|---|---|---|
| Kernel language | `max/mojo/max/gpu/` (`primitives`, `intrinsics`, `memory`, `sync`, `compute`) | Ordinary Mojo functions, including generics and compile-time evaluation, are compiled for a device target. Vendor differences are compile-time branches. |
| Target description | `max/mojo/max/_plugin/{cuda,hip,metal}/targets.mojo` | Per-vendor target tables. The CUDA table has 18 `nvptx64-nvidia-cuda` entries; the Metal table has 12 `air64-apple-macosx` entries, six for Metal 3.2 / AIR 2.7 and six for Metal 4.0 / AIR 2.8. |
| Device code generation | `Mojo/lib/Compiler/ObjectCompiler/Target/max_license/{NVPTX,AMDGPU,Metal,Plugin}` | Host and each vendor are peer target backends selected by triple. NVPTX emits PTX through LLVM and compiles it to a cubin. AMDGPU reuses the host object-and-link path to produce an hsaco ELF. Metal is described below. A plugin backend resolves triples owned by dynamically loaded compiler plugins. |
| Device runtime | `SDK/include/HAL/M_driver_*.h`, `Target/lib/{CPU,CUDA,HIP,Metal}`, `MLRT/lib/Driver/DeviceContext/` | A versioned C interface (driver, device, context, queue, event, flag, function, memory) that each vendor plugin implements by translating to the vendor driver API. The newer driver is still incubating; MAX runs on the older per-device `DeviceContext` by default. |
| Kernel library and serving | `max/kernels/src/` (`linalg`, `nn`, `kv_cache`, `quantization`, `graph_compiler`, ...), vendor bindings (`_cublas`, `_cudnn`, `_rocblas`, `_miopen`), MAX graph compiler and pipelines | This layer, not the compiler, is what makes "run a model" work. |

The Metal backend deserves separate attention because it is the only one
without an upstream LLVM target. Its own debugging manual states that Apple GPU
support was obtained by reverse engineering. In source:

- `MetalAIRPass.cpp` (3,836 lines) with `InstructionRewrite`,
  `MetalModuleRewritePass` and `MetalVerifier` rewrites LLVM IR into Apple's
  AIR dialect: triple, AIR metadata, kernel attributes and intrinsic mapping.
- AIR must be serialized in an old LLVM bitcode dialect because Apple's
  compiler still requires typed pointers, so the compiler carries its own
  version-specific bitcode writers.
- `MetalBackend.cpp` links a metallib with `xcrun metal`, or packages it with
  the in-tree `MetallibWriter.cpp` (210 lines) when the Metal toolchain is
  absent. The writer's header comment documents the container layout it emits
  and notes that reflection sections are not required for loading.

Two facts follow. First, a metallib container can be produced without Apple's
toolchain, and the container itself is small. Second, the expensive part is
AIR: an undocumented, versioned LLVM-bitcode dialect that Modular maintains
with several thousand lines of rewriting and a custom bitcode writer.

Licensing was not analyzed. The backend files carry Apache-2.0-with-LLVM-
exception headers but live in directories named `max_license`, and the
top-level `LICENSE` says portions of MAX are under a separate MAX License.
Check which license governs a file before reusing source or format tables
from it.

## 2. pcc at the baseline, with a later owned-route update

### 2.1 What exists

- **Frontend marker.** `pcc/gpu.py` defines `@gpu.kernel` and four types
  (`ptr_f32`, `i32`, `u32`, `f32`). `pcc/kernel_ir/entry.py` imports vector add
  and a finite scalar, indexed and `if/else` subset; other syntax fails closed.
- **Kernel IR.** `pcc/kernel_ir/ir.py` is a kernel-only IR whose validator
  rejects any Python-object, GC-frame, weakref or finalizer escape. A
  TIRx-compatible freeze, an optional replayable schedule, a host/device split
  and a Metal finalizer follow it.
- **Device code.** The finalizer emits Metal Shading Language *source text*.
  Apple's compiler turns that text into device code, either inside the OS at
  run time (`newLibraryWithSource`) or offline through `xcrun metal` and
  `xcrun metallib`.
- **Resource model.** `PccBufferHandle`, `PccPackedArgs` and `PccFenceToken`
  with fence-deferred release, DLPack capsule export and import, and a
  GC-facing external-resource seam in `pcc/gpu_gc/`.
- **TileLang-shaped import.** A strict source-subset parser that produces
  Kernel IR; it does not import or run TileLang or TVM.
- **Backend selection.** `--gpu-backend` accepts `none` or `metal`
  (`pcc/backend/gpu_dispatch.py`). CUDA, HIP, Vulkan and WebGPU are not
  modeled.
- **Oracles only.** `pcc/gpu_gc/` and `pcc/dist/` are CPU oracles; the vLLM
  and MiniMind gates under `tests/gpu_packages/` are metadata surrogates whose
  `pcc-metal-kernel` mode is deliberately never claimed.

### 2.2 Claim level reached

`pcc/kernel_ir/gpu_claims.py` defines levels 0 to 6. The July design documents
record level 4 (real command buffer, completed fence, readback equal to the CPU
oracle) for a copy kernel, an imported scalar GEMM with several layout and
split-k variants, and an 8x8 simdgroup GEMM microkernel, all through the
runtime-source path on one local machine. Gates for level 5 (pcc1-native
launcher) and level 6 (five-GC lifetime parity) now exist under
`tests/gpu_hardware/`. No current-source receipt for any level was read or
produced here, and `docs/status.md` has no GPU section.

### 2.3 External tools on the current GPU path

Under the pcc1 ownership contract these are migration defects, not accepted
dependencies.

| Step | Tool | Where |
|---|---|---|
| MSL to AIR, AIR to metallib on the CLI route | `xcrun metal`, `xcrun metallib` | `pcc/backend/metal.py`, called from `pcc/kernel_ir/entry.py` |
| Host launch bridge | `xcrun clang` compiling Objective-C | `pcc/backend/metal.py`, generated sources in `pcc/kernel_ir/metal_source_runtime.py` and `metal_launch.py`, `pcc/backend/gpu_metal_runtime.m` |
| Embedding the metallib | linker `-sectcreate __PCCMETAL __metallib` | Metal branch of `_compile_python_routed` in `pcc/frontends/python/pipeline.py` |
| CPython-side launch | `ctypes` library loading and callbacks | recorded as blockers in `pcc/kernel_ir/pcc1_metal_preflight.py` |
| Level-5 gate | a C shim built with `cc` | `tests/gpu_hardware/test_metal_pcc1_launch_real.py` |
| pcc-gui rendering | `xcrun clang` compiling `pcc_gui_metal_render_bridge.m` | pcc-gui `pcc_gui/native/`, Harness `build.sh` |

`pcc/runtime/py/freestanding_metal_runtime.py` already moves the *call* of a
prebuilt bridge into freestanding pcc-Python. The bridge itself is still
Objective-C built by clang. No pcc-Python module in pcc or pcc-gui calls the
Objective-C runtime directly today.

Other limits read from source: `--gpu-backend=metal` accepts `@gpu.kernel` only
in single-file compiles, and the July documents record offline metallib
production as blocked on the development machine by a missing Metal Toolchain
component.

### 2.4 Static observation, unconfirmed

Strata documents that Metal silently skips a dispatch when the pipeline's
`maxTotalThreadsPerThreadgroup` is below the requested threadgroup size, and it
checks every dispatch for that reason. In pcc only the legacy demo runtime
`pcc/backend/gpu_metal_runtime.m` reads that limit; the Kernel IR bridge
emitters do not mention it. The CPU-oracle readback in pcc's gates would expose
a skipped dispatch, but a launch without an oracle would not. This is a reading
of source, not a reproduced failure.

### 2.5 Subsequent owned runtime and public CLI evidence

The baseline bridge limitation is no longer true of every route. The owned
`pcc/gpu_runtime/metal_device.py` calls the Objective-C runtime through typed
C ABI intrinsics. `8c4e17e79` connects `@gpu.kernel` / `gpu.launch` to it through
`pcc/kernel_ir/gpu_host_launch.py` and the normal Python pipeline. The resulting
native program embeds MSL and calls the packet runtime directly, without an
Objective-C source bridge, `.air`/`.metallib` or subprocess launcher. It checks
the pipeline's threadgroup limit; the older observation in 2.4 concerns the
legacy bridge emitters, not this runtime.

`48b912bd8` rejects writable buffer aliases and buffer types outside the
published bytes/bytearray contract. On local Darwin arm64, the five new gates
in `tests/gpu_hardware/test_gpu_kernel_launch.py` execute their binaries under
observed GC0–4: SAXPY and readback, invalid arguments, allowed read-only aliases,
no-subprocess compilation/linking with a verified prebuilt runtime, and native
f32 packing against `ctypes.c_float`. With the CLI test migration `b8aefaeff`,
the final selected affected-directory run is 561 passed. The initial offline
reference/toolchain failures were resolved by installing the official component
and matching Xcode 27.2, SDK 27.2 and Metal Toolchain 27B47. The legacy metallib
and TileLang reference checks now run too; they do not establish owned execution.
The MLX DLPack node remains collection-gated for its missing dependency, and
four pcc1-dependent files were not run. [Current status](../status.md) names
those boundaries and the repro. This is not an all-gates pass.

This proves the selected host-pcc0/self route and synchronous native execution,
not pcc1-native compilation, async/five-GC resource lifetime, Strata engine
support or owned GPU machine-code emission. Metal still compiles MSL through
`newLibraryWithSource:options:error:`. Acceptance of that platform boundary
under the pcc1 contract remains a maintainer decision. The legacy `pcc-metal`
and pcc-gui bridge paths are not replaced by this evidence.

## 3. Layer comparison (baseline `7545dce5a`)

| Layer | Mojo | pcc today | Reachable for pcc | Route |
|---|---|---|---|---|
| Kernel language | whole language with generics | finite marked subset | yes, incrementally | grow Kernel IR semantics first, then the frontend subset |
| Vendors | NVIDIA, AMD, Apple | Apple only | Apple now; others only with new emitters | see section 4 |
| Device code generation | own compiler through LLVM targets; reverse-engineered AIR | MSL text handed to Apple's compiler | not by Mojo's route | see section 4 |
| Host runtime | C++ driver plugins behind a C interface | Objective-C bridges built by clang | yes | pcc-Python over the Objective-C runtime |
| Resource lifetime | driver-level buffers, queues, events | handles, packed arguments, fences, GC seam | already ahead in specification | prove it at levels 5 and 6 |
| Kernel library | large tuned library plus vendor BLAS/DNN | copy, fill, reduce, scalar and 8x8 GEMM | long | reuse foreign kernel sources as labeled assets first |
| Graph compiler and serving | MAX | none | not needed for one engine | a single-model engine does not need a graph compiler |

The gap that matters for running a model is not the compiler. It is the host
runtime that pcc does not yet own and the kernel library that pcc does not yet
have.

## 4. Device-code ownership under the pcc contract

The contract forbids LLVM and external compilers on the pcc1 path, including
bundled ones. The options, with their honest status:

- **A. MSL source through the OS compiler.** This is what pcc does and what
  Strata ships: its Metal runtime compiles MSL at run time because the Command
  Line Tools carry no offline `metal` compiler. Whether the OS shader compiler
  counts as the platform boundary, like the dynamic loader, or as an external
  compiler is a contract decision that the current documents do not settle.
- **B. Owned AIR and metallib emission.** Mojo shows this is possible and that
  the container is the easy part. The AIR dialect is undocumented, versioned
  and tied to an old bitcode format. For pcc it would mean a hand-maintained
  bitcode writer for a format Apple may change. High cost, format risk.
- **C. Owned PTX emission for NVIDIA.** PTX is a text virtual-assembly format.
  A pcc-owned PTX emitter would fit the same pattern as the owned assemblers.
  Whether the CUDA driver alone can load PTX without a vendor compiler library
  was not verified here; Mojo itself compiles PTX to a cubin through a bundled
  or system compiler.
- **D. Owned SPIR-V emission.** SPIR-V is a documented binary format accepted
  by Vulkan compute on NVIDIA, AMD and Intel. It can avoid an external
  command-line shader compiler, at the price of a new emitter and a Vulkan
  host runtime; the driver still translates IR to vendor-native instructions.
  Owned SPIR-V emission is not proof of owned GPU machine-code generation.
  Not explored further.
- **E. Labeled external provider.** `docs/design/pcc-gpu-owner-backends.md`
  already allows an explicitly selected, pinned provider to own execution
  while pcc keeps semantic, ABI and lifetime ownership. A vendor runtime
  compiler would have to enter through that door, never as a silent default.

Recommendation, labeled as the author's, not a maintainer decision: treat A
as the Metal boundary, own everything on the host side of it and defer B.
C and D are candidate emission routes to other vendors, not established
contract-compatible end-to-end pipelines; driver-side code-generation ownership
also needs an explicit decision.

## 5. Strata

### 5.1 What it is

Strata is an MIT-licensed local inference product: its own engine for a few
model families, an OpenAI- and Anthropic-compatible server on loopback, a
terminal tool and a desktop application. Its three parts, by its own technical
reference:

- **App**: a Tauri shell (`apps/desktop`) with a Node host and web UI
  (`apps/host`).
- **Terminal**: Python (`strata.py`, `strata_cli/`, `strata_control/`).
- **Engine and server**: C++ with CUDA, HIP, SYCL and Metal backends (`src/`,
  `include/`, `metal/`, `sycl/`) and a Python server (`serve/`).

Language statistics reported by GitHub: C++ 19.6 MB, Python 14.8 MB, CUDA
5.9 MB, JavaScript 4.0 MB, TypeScript 1.6 MB, Metal 0.76 MB, Rust 0.18 MB,
Objective-C++ 0.12 MB.

### 5.2 How it uses Harness

Strata runs chats as DeepSeek Harness sessions. The app host starts a pinned
upstream Harness process on loopback and talks to its gateway
(`apps/host/server/agent/`). The profile in `integrations/harness/profile/`
is a small patch over the upstream bundles that does two things: it sets the
default model to a local route, and it declares that route as an ordinary
OpenAI-completions endpoint on `127.0.0.1` with explicit limits. Its comments
record two constraints worth keeping:

- the local route must never fall back to a cloud model, so no cloud route is
  declared in that profile;
- the Harness system prompt and tool definitions already occupy roughly 29K
  tokens, so a 32K context leaves almost no output budget.

This is the same upstream that the pcc Harness ports, at a different pin. The
"local model mode" is therefore an existing, exercised configuration of the
upstream product, not a new design.

### 5.3 Metal engine facts relevant to pcc

- Kernels are MSL source files (29 files, about 458 KB under `metal/kernels/`)
  compiled at run time. One Objective-C++ file implements a thin runtime of
  buffers, pipelines and dispatches; everything else is C++ against that
  header. The engine reuses the PC host sources unchanged through a
  compatibility header layer and metal-cpp.
- Weights are file-backed pages wrapped as no-copy device buffers and kept
  resident; large buffers are split into overlapping chunks because of the
  device's maximum buffer length. Its guide states that Metal lets one process
  keep about three quarters of unified memory resident.
- When a model does not fit, experts stream from the SSD into a bounded arena
  with a host/device handshake inside one command buffer.
- It requires macOS 15 for residency sets.

These map onto concepts pcc already specifies: external buffer handles that the
collector never moves or scans, fences as the release edge, and recorded device
limits as launch-plan inputs.

### 5.4 Mapping to pcc owners

| Strata part | Technology | pcc-side owner | Status |
|---|---|---|---|
| Desktop shell and web UI | Tauri, Node, React | pcc-gui declarative native GUI | the Harness port already replaces the upstream web client this way; Strata-specific views (model choice by memory, downloads, engine status) would be new |
| Agent runtime | upstream Harness (Node) | the pcc Harness port | in progress; translation by the TAG compiler is ongoing |
| Local route | OpenAI-compatible HTTP on loopback | Harness model provider registry | `DeepSeekSseProvider` already takes a base URL; the route, limits and reasoning format are the delta |
| Server | Python under CPython | either pcc-compiled Python or no server at all | an in-process provider removes the HTTP hop for a single desktop application |
| Engine host | C++20, ggml, metal-cpp, one Objective-C++ file | not ownable soon | the C++ frontend is still working through TinyXML2; Objective-C++ is outside every pcc frontend |
| Kernels | hand-written MSL | pcc launcher as host, Kernel IR as eventual generator | usable as labeled assets through the runtime-source path |
| Model files | GGUF and custom packs | a pcc-Python reader | not started |

"Bringing in Strata" can therefore mean three different things with very
different costs: adopting its *protocol and product shape* (cheap), reusing its
*kernel sources and format knowledge* as assets (moderate, license notices
required; ggml headers are prepended to some kernels), or owning an equivalent
*engine* (large). Only the first two are compatible with keeping pcc and
pcc-gui as the owners underneath in the near term.

## 6. Harness local-model mode: staged direction

Each stage has its own ownership label. A lower stage is never evidence for a
higher one.

**Stage 0: loopback route to an external engine.**
Add a local provider route to the Harness port that speaks OpenAI-compatible
chat completions with streaming to a local server. Inference is owned by an
external process and labeled so. Needs: a loopback HTTP and SSE client (no TLS
on loopback), a declared context window and output budget, a typed
model-unavailable error and no cloud fallback. Exit evidence: one prompt and
one tool call through the pcc1-built Harness against a running local server.
No GPU work in pcc.

**Stage 1: an owned Metal launcher.**
Replace the clang-built Objective-C bridges with pcc-Python calls into the
Objective-C runtime, starting with a block-free synchronous path (device,
library from source, pipeline, command buffer, dispatch, wait). The same layer
would retire the pcc-gui render bridge. Exit evidence: the existing copy and
GEMM kernels at level 5 with no external compiler on the launch path, then
level 6. This is the prerequisite for every in-process stage. Feasibility of
the required call shapes and of Objective-C blocks from pcc-Python is not
established.

Update, same day: the block-free synchronous path now exists as
`pcc/gpu_runtime/metal_device.py` and is executed on a real device by
`tests/gpu_hardware/test_metal_device_runtime.py` (host-pcc0 evidence). The
statements above and in section 2.3 describe the tree before that change.
Blocks, the pcc-gui bridge, level 5 and level 6 remain open; the follow-on
design is [the local-model architecture](../design/pcc-local-model-architecture.md).

**Stage 2: foreign kernels as assets.**
Load unmodified MSL kernel sources through the pcc launcher, each with a CPU
oracle, zero-copy weight buffers through the external-resource seam, and a
dispatch-fit check. This needs a claim label distinct from Kernel IR compiled
kernels, for example `kernel_source=foreign-msl-asset`.

**Stage 3: one small complete model in-process.**
A small dense model, token-exact against a CPU oracle, under all five
collectors, integrated with virtual threads so a device wait never blocks a
carrier. The MiniMind gate shape in `docs/design/pcc-vllm-minimind-gates.md`
is the existing placeholder for this.

**Stage 4: a Strata-class model family.**
Quantized expert formats, expert caching, SSD streaming, draft-and-verify
decoding and the product views around them. Kernel IR replaces assets one
kernel at a time under the existing parity matrix.

## 7. Open decisions

These belong to the maintainer. Each recommendation is the author's.

1. Is the OS Metal shader compiler an accepted platform boundary for the pcc1
   ownership contract? Recommendation: yes for MSL source, with the host side
   fully owned.
2. May foreign MSL kernels ship as labeled assets, or must every kernel come
   from Kernel IR? Recommendation: allow them with a distinct label.
3. Is an external engine process acceptable for the first local-model mode?
   Recommendation: yes; it is the configuration Strata already exercises.
4. Is vendor breadth in scope at all? Recommendation: defer; when it is, use
   an owned PTX or SPIR-V emitter rather than a vendor compiler.
5. Where do stages 1 to 4 sit relative to the frontend and GC work? Stage 0 is
   Harness-side only; every later stage competes with the spine.

## 8. Original proposed probes (historical, not the active task queue)

Proposed only; none was run.

- Renew level 4 on current source with `tests/gpu_hardware/test_metal_claim_levels.py`
  under `PCC_GPU_HARDWARE_STRICT=1`.
- Run the level-5 gate `tests/gpu_hardware/test_metal_pcc1_launch_real.py` and
  record which external tools it still invokes.
- A pcc-Python Objective-C runtime probe: create a device and compile a
  one-line MSL library with no Objective-C source. This answers the Stage 1
  feasibility question directly.
- One small foreign MSL kernel through the runtime-source path against a CPU
  oracle.
- A Harness provider check against any local OpenAI-compatible server.

## 9. What this document does not claim

- It does not claim that pcc has Mojo's GPU capability, whole-program GPU
  execution, or any model runtime.
- Only the executed cases named in section 2.5 and current status are renewed;
  this is not blanket renewal of GPU claim levels. Older July results remain
  dated evidence.
- It does not claim that Strata's engine, kernels or performance figures were
  verified.
- The original baseline reading did not establish an owned Objective-C runtime
  launcher; section 2.5 now supplies narrow host-pcc0 execution evidence.
  Neither the original reading nor that update establishes that PTX loads
  without a vendor compiler or that a SPIR-V route works for pcc.
