// PCC-owned C++17 <errno.h>, N4659 [depr.c.headers], [cerrno.syn].
// Share the target's exact error constants and modifiable errno lvalue with
// <cerrno>. Never borrow the fake C-header's ABI or synthesize a global int.
#ifndef _PCC_CXX_ERRNO_H
#define _PCC_CXX_ERRNO_H
#include "cerrno"
#endif
