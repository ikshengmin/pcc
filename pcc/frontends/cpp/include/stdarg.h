// PCC-owned C++17 <stdarg.h>, N4659 [depr.c.headers], [cstdarg.syn].
// Share the nominal SysV cursor and checked builtins with <cstdarg>/<cstdio>.
// Unsupported target ABIs remain explicit errors in __pcc_va_list.h.
#ifndef _PCC_CXX_STDARG_H
#define _PCC_CXX_STDARG_H
#include "__pcc_va_list.h"
#define va_start(ap, last) __builtin_va_start(ap, last)
#define va_arg(ap, type) __builtin_va_arg(ap, type)
#define va_copy(dst, src) __builtin_va_copy(dst, src)
#define va_end(ap) __builtin_va_end(ap)
#endif
