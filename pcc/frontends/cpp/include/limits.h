// PCC-owned C++17 <limits.h>, N4659 [depr.c.headers], [climits.syn].
// Reuse the exact target integer-width/signedness and promoted macro values
// from <climits>. The existing compile-time MB_LEN_MAX capacity declaration
// is not a qualified locale policy; no fake C-only macros are acquired.
#ifndef _PCC_CXX_LIMITS_H
#define _PCC_CXX_LIMITS_H
#include "climits"
#endif
