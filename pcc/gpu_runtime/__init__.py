"""Device host runtimes written in freestanding pcc-Python.

Each module here owns the host side of one accelerator API behind a C ABI:
opening a device, allocating device-visible memory, building a pipeline from
device code and launching it. The modules use only compiler intrinsics and the
platform's dynamic loader, so no bridge written in another language and no
external compiler takes part.

``metal_device`` is the first device. The modules are compiled on demand; they
are not part of the pcc-Python runtime archive.
"""
