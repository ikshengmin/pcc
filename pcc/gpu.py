from __future__ import annotations

"""User-facing markers for pcc GPU kernels.

These objects are compile-time markers.  Host CPython can import them so source
files remain inspectable, but pcc lowers ``@gpu.kernel`` functions only when a
device backend such as ``--gpu-backend=metal`` is selected.

A kernel runs once per thread id::

    @gpu.kernel
    def add(a: gpu.ptr_f32, b: gpu.ptr_f32, out: gpu.ptr_f32, n: gpu.u32):
        i = gpu.thread_id_x()
        if i < n:
            out[i] = a[i] + b[i]

    gpu.launch(add, a, b, out, n, count=n)

``gpu.launch`` takes the kernel, one argument per kernel parameter and the
number of thread ids as ``count``. A ``ptr_f32`` argument is ``bytes`` or
``bytearray`` holding little-endian f32 elements; a buffer the kernel writes
must be a ``bytearray`` and is updated in place when the launch has completed.
Threads are launched in whole groups, so thread ids may exceed ``count``; a
kernel must guard its accesses as ``add`` does.
"""


class _GpuType:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return "gpu." + self.name


ptr_f32 = _GpuType("ptr_f32")
i32 = _GpuType("i32")
u32 = _GpuType("u32")
f32 = _GpuType("f32")


def kernel(fn):
    fn.__pcc_kernel_ir_entry__ = True
    return fn


def thread_id_x() -> int:
    raise RuntimeError("pcc.gpu.thread_id_x() is only valid inside a GPU kernel")


def launch(kernel, *args, count: int) -> None:
    raise RuntimeError(
        "pcc.gpu.launch() must be lowered by pcc --gpu-backend=metal"
    )


def run_add_f32_demo() -> int:
    raise RuntimeError(
        "pcc.gpu.run_add_f32_demo() must be lowered by pcc --gpu-backend=metal"
    )
