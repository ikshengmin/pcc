# Unchanged upstream boundary input

`meson_adder.rs` is byte-for-byte the already-vendored source:

`projects/numpy-2.4.4/vendored-meson/meson/test cases/rust/15 polyglot sharedlib/adder.rs`

- Source repository revision: `e9b32227d519094795821e704c276a83a973bdae`
- Git blob: `ec4d1cc138b61378a9486d9e9daaf67f43afef05`
- SHA-256: `654d4f498384c21d51627908a842fe8454e5918ef7ba9317a740d7623c9c0933`
- Existing upstream license: `projects/numpy-2.4.4/vendored-meson/meson/COPYING`
  (Meson, Apache License 2.0).

The test intentionally proves an explicit **unsupported** boundary, not program
execution. The source is not stripped, renamed, or translated. Its repr(C)
record parses, after which the extern declaration block is still unsupported.
Borrowed parameters, unsafe operations, attributes and external-symbol linkage
remain later dependencies. No original source was changed to pass the frontend.
