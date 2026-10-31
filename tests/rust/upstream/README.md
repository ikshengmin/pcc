# Unchanged upstream boundary input

`meson_adder.rs` is byte-for-byte the already-vendored source:

`projects/numpy-2.4.4/vendored-meson/meson/test cases/rust/15 polyglot sharedlib/adder.rs`

- Source repository revision: `e9b32227d519094795821e704c276a83a973bdae`
- Git blob: `ec4d1cc138b61378a9486d9e9daaf67f43afef05`
- SHA-256: `654d4f498384c21d51627908a842fe8454e5918ef7ba9317a740d7623c9c0933`
- Existing upstream license: `projects/numpy-2.4.4/vendored-meson/meson/COPYING`
  (Meson, Apache License 2.0).

`meson_zero.c` and `meson_zero_static.c` are also unchanged, from the same
revision and directory's `zero/zero.c` and `zero/zero_static.c`:

- `zero.c`: blob `02672f3946d4ebc4bb0a1d1eafa73cbb9ca9e31e`, SHA-256
  `fc2751e904e85ba2e1ce45478534270489071fda1b7266df9ec409b2ae10ec31`.
- `zero_static.c`: blob `7f14fb4d65d1601761bd770773e2cead53be8240`, SHA-256
  `862ddec242b5a9d0cbb741af84060ca46a6958c38987d92eb86d9a2a32b96e59`.

The three original inputs enter the owned Rust and C frontends, respectively;
no zero-function stub, source rewriting or token stripping replaces them. The
Linux object-boundary test links all three with an independent ABI observer.
Darwin tests inspect actual cross-emitted objects, not execution on a Mac.

This is **not the whole Meson project**: the original adder.c/addertest.c also
require malloc/free, shared-library construction and the project's build graph.
Those remain a separate shared-runtime/linker qualification, not a new private
Rust allocator. No original source was changed to pass the frontend.
