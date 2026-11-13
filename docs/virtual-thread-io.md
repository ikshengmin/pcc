# Native virtual-thread fd waits

These are PCC runtime APIs, not additions to the Python language standard.
They run through the owned virtual-thread scheduler and its shared I/O waitset.

`block_current_on_fd(fd, events, timeout_ms)` first probes readiness and can
continue without suspending. Its existing C ABI returns 1 for that case.

`wait_current_on_fd(fd, events, timeout_ms)` registers interest without that
extra probe and suspends. Use it after a nonblocking read, write or accept
reports would-block. It may suspend even when readiness has just arrived.
Registration must observe readiness already present or arriving concurrently;
the operation must be retried after wakeup. Readiness is not a promise that
the retry succeeds.

Both operations use event bit 1 for read and 4 for write; zero selects read.
A negative timeout waits without a deadline. A nonnegative timeout supplies
the same relative millisecond budget as the existing wait operation. Timeout
wakeup does not perform the I/O operation or reset the caller's own deadline.
The caller owns the descriptor and must keep it open, without numeric reuse,
until wakeup or cancellation. Completing/cancelling the task must remove its
registration and release its scheduler root exactly once.

`py_virtual_thread_wait_on_fd` returns 0 after registration or -1 on failure.
The eager probe API and generation-checked TCP API retain their contracts.
All entries share one registration, cancellation and root-ownership protocol.

Focused native/reference coverage lives in
`tests/python/gc_production_contract/test_vthread_io_waitset_runtime.py`:
readiness before and after registration, timeout, completion cancellation,
and the unchanged eager result are checked under every collector. Import and
effect propagation are checked in `test_vthread_observed_fd_wait.py`.
Native-pcc1 and full-feature qualification remain separate gates.
