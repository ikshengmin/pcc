// PCC-owned C++17 <stdio.h>, N4659 [depr.c.headers], [cstdio.syn].
// Reuse the owned global declarations, FILE ABI and macros from <cstdio>.
// This spelling does not implement missing runtime operations, fpos_t storage,
// capacity guarantees or va_list ABIs for additional targets.
#ifndef _PCC_CXX_STDIO_H
#define _PCC_CXX_STDIO_H
#include "cstdio"
#endif
