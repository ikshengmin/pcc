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

## Rust 1.90.0 unchanged frontier sources

The files under `rust-1.90.0/` were read from the official `rust-lang/rust`
release tag **1.90.0** through authorized source access, before implementing the
pattern domain. Each complete file is pinned independently by Git blob and
SHA-256 (no extraction, token removal, or macro replacement):

| Local file / original path | Git blob | SHA-256 |
| --- | --- | --- |
| `core_cmp.rs` / `library/core/src/cmp.rs` | `1b9af10a6fda55e5e4ebe07a2a5d83c8a7c032e5` | `c48745bb89c4d3ef361923ac72346133f81a64c112f7bfc625f4beced52016c1` |
| `exhaustive-bool-match-sanity.rs` / `tests/ui/binding/exhaustive-bool-match-sanity.rs` | `9bdab3ad02c86a817135421f04b86d87bf3e7128` | `43a9442c59cf2640ef85947545c0a01954bcd5697f993183d688501fb57f80fb` |
| `LICENSE-APACHE` | `1b5ec8b78e237b5c3b3d812a7c0a6589d0f7161d` | `62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a` |
| `LICENSE-MIT` | `a2070c4be7acd5316f8d0357ca3c12419a2f3c62` | `b71bd43a069ca0641a9ecfe585ca7b3c53b5cc1608f8b68321168698e28b5ea1` |

Rust is dual MIT/Apache-2.0 licensed; both original license texts accompany these
files. Source URLs:
[core cmp](https://github.com/rust-lang/rust/blob/1.90.0/library/core/src/cmp.rs),
[match test](https://github.com/rust-lang/rust/blob/1.90.0/tests/ui/binding/exhaustive-bool-match-sanity.rs).

The complete `cmp.rs` first fails at documentation attributes and additionally
needs imports, traits/derives, generics, impl/methods, `repr(i8)`, language items
and owned intrinsics. Its `Ordering` declaration (lines 384–400) and `reverse`
match (562–568) identify actual enum/match prerequisites; they are not extracted
into a substitute core. The complete official test now parses its bool-tuple
match and still fails at original `assert_eq!` macro calls. Its own `main` has
not run. Expected capability-diagnostic tests are **not upstream execution
success**. PCC's separately authored enum/match programs are specification
fixtures, not these upstream programs or an implementation of core/alloc.

## Original payload dependencies, not replacement implementations

`rust-1.90.0/core_option.rs` and `core_result.rs` are the full official
`library/core/src/option.rs` and `library/core/src/result.rs`, read at immutable
release commit `1159e78c4747b02ef996e55082b704c09b970588` before implementing
payload variants. They use the same dual-license originals retained above.

- Option: blob `ed070fbd227464718f1668e1a16cf7ce2c176d48`, SHA-256
  `53b549b18552144e911416aa2773e8b726da88c190d5cb27007c3c08740894a5`, 90,809 bytes.
- Result: blob `f65257ff59b923bb2d83c7bd7371a730990769da`, SHA-256
  `768b0584d514fce9255a762e76bb51eb1646b7bcabd6db660752960321bfc51f`, 69,766 bytes.

`Option` at line 593 and `Result` at line 548 require generic tuple-payload enum
constructors. Their original methods need match bindings plus references,
trait/closure bounds, impl/method syntax, macros and owned lang items/intrinsics.
Both complete files still fail at their initial documentation attributes. No
methods are extracted, renamed or rewritten to claim original core success.
See [Option original](https://github.com/rust-lang/rust/blob/1159e78c4747b02ef996e55082b704c09b970588/library/core/src/option.rs)
and [Result original](https://github.com/rust-lang/rust/blob/1159e78c4747b02ef996e55082b704c09b970588/library/core/src/result.rs).
