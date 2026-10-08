# pcc

**A native compiler for Python and C, written in Python.**

The goal is to preserve Python's syntax and behavior without requiring
libpython at runtime.

## Quick start

On Linux x86_64, with Git and CPython 3.15 (recommended):

```bash
git clone https://github.com/ikshengmin/pcc.git
cd pcc

printf 'print("Hello, PCC!")\n' > hello.py
python3.15 -m pcc hello.py -o hello
./hello
```

The first compilation builds the runtime and can take several minutes.
CPython runs the compiler; `hello` is a native executable that prints `Hello, PCC!`.

## Inside pcc

- [Frontends](pcc/frontends/): Python and C parsing, analysis and code generation
- [Backend](pcc/backend/): an in-tree native code generator
- [Runtime](pcc/runtime/): Python objects, native threads and five garbage collectors

## Status

pcc is experimental. Native examples work on Linux x86_64. Broader Python
compatibility, native compiler self-hosting and other platforms are still
being qualified.

## Documentation

- [Design goals](docs/project-intent.md)
- [Architecture](docs/architecture/01-overview.md)
- [Developer reference](docs/developer-reference.md)
- [Verification status](docs/verification-status.md)
- [Book: English and 中文](books/README.md)

## License

[MIT](LICENSE)
