# Private measured diagnostic, not a production collector

The opt-in kernel admits only float(24) or str(41–128), into two bounded fixed
cell-size lanes. It never adds a young object record; whole survivor pages
remain in place. The surrounding collector still accounts for pressure. Before
recycling any young page, the same stopped epoch must cover registers, exact
roots, conservative interiors, each participating carrier, and the strong
outgoing edges/known raw aliases of the old heap. Pin/static/foreign exports
may retain a whole page; opaque native use irrevocably refuses admission.
This implements a full-old-graph *reference*, not an efficient remembered set.

Focused evidence, Linux x86-64, owned native compiler/assembler/linker, Mac unrun:
a 206-member static archive for the complete source linked both the C
window witness and canonical native handler. All five backend controls,
real GC3 whole-page reclamation (386 managed allocations, 4 pages
recycled, 1 promoted with occupied co-tenants), unregistered/blocked and
revoked paths passed. GC3 canonical in-process w1 and w4 each validated 10,000
responses with 200 warmups, but the stock compiler did not grant this explicit
archive/virtual-thread/raw-import executable its pre-existing closed-world
proof. Both generated runs consequently refused setup (window state -3,
runtime domain 0, zero admissions and zero minors, although each did see
five stock STW). These do **not** measure this experiment. No HTTP #4 rerun,
Mac run or GC0 throughput conclusion. The wrapper does not override a denied
process, C/unknown input or failed stop proof.

Unit/source checks: `pytest -n 0 tests/python/test_runtime_abi_chunking.py
tests/experimental_exp2_leaf/test_process_window{,_integration}.py`. Native
test source `process_window_native.c`: run from a fresh subprocess per
PCC_GC_BACKEND=0,1,2,3,4. GC3 mode d = unproven C host, r = opaque null
scope exit, b = blocked carrier, p = controlled closed native fixture. Production
HTTP/compilers and ordinary selecting environments do not install this scope.
