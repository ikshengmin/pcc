# PCC as a Meson configuration compiler

For a trusted source checkout, `scripts/pcc_meson_setup.py` connects an independently supplied **Meson 1.9.2** to PCC's C11 and C++17 drivers on native x86-64 Linux. Meson is a host development tool here, not a compiler dependency or a native `pcc1` build provider. No file in Meson or the project is patched. The target compiler processes import PCC and the Python standard library only.

Set `PCC_RUNTIME_ARCHIVE` to the absolute path to a source-, target- and configuration-matched `libpy_runtime_pcc_py.a`. The driver uses the normal strict runtime admission checks. Then run, with a new output directory outside the source:

```sh
python scripts/pcc_meson_setup.py \
  --meson-source projects/numpy-2.4.4/vendored-meson/meson \
  /path/to/project /tmp/pcc-project-build
```

Meson emits and runs its own compiler sanity checks and executes the project's actual compiler queries. Compile and link errors stay errors. `--configuration-only` explicitly selects Meson's ordinary `none` backend for a *targetless* project, for example one that configures a header from `cc.run()`; it is not an alternative way to qualify a build containing targets.

The registered driver names are `pcc`, `pcc-self` (fixed static ELF executable linker), and `pcc-ar` (indexed archive writer). To call these tools directly from a PCC checkout:

```sh
python scripts/pcc_meson_setup.py --tool cpp -std=c++17 -c library.cpp -o library.o
python scripts/pcc_meson_setup.py --tool ar --create libexample.a library.o
python scripts/pcc_meson_setup.py --tool c -std=c11 -c main.c -o main.o
python scripts/pcc_meson_setup.py --tool c main.o libexample.a -o program
```

Unsupported options and interleaved source/object/archive order fail rather than silently selecting another tool or precision. There is no fallback to a system compiler, linker, archiver, target `libm` or downloaded generator. Use only trusted projects and Meson distributions: Meson build descriptions themselves can run programs; this adapter is not a security sandbox. Header dependency-file generation for native target graphs, dynamic/shared linkage, implicit named libraries, other languages and cross-compilation are not qualified here.

For the unmodified NumPy 2.4.4 source, completing Meson's C and C++ sanity checks is **not** success for the NumPy source gate. The project also requires Cython and further target probes. No `_numpyconfig.h`, package or Stage1/2/3 result is substituted for those checks.
