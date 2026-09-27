#ifndef _FAKE_LIMITS_H
#define _FAKE_LIMITS_H

#include "_fake_defines.h"
#include "_fake_typedefs.h"

#ifndef MB_LEN_MAX
#define MB_LEN_MAX 16
#endif

#ifndef MB_CUR_MAX
#define MB_CUR_MAX 16
#endif

#ifndef CHAR_BIT
#define CHAR_BIT 8
#endif

#ifndef SCHAR_MAX
#define SCHAR_MAX 127
#endif

#ifndef SCHAR_MIN
#define SCHAR_MIN (-SCHAR_MAX - 1)
#endif

#ifndef UCHAR_MAX
#define UCHAR_MAX 255
#endif

#ifndef CHAR_MAX
#if defined(__CHAR_UNSIGNED__)
#define CHAR_MAX UCHAR_MAX
#define CHAR_MIN 0
#else
#define CHAR_MAX SCHAR_MAX
#define CHAR_MIN SCHAR_MIN
#endif
#endif

#ifndef SHRT_MAX
#define SHRT_MAX 32767
#endif

#ifndef SHRT_MIN
#define SHRT_MIN (-SHRT_MAX - 1)
#endif

#ifndef USHRT_MAX
#define USHRT_MAX 65535
#endif

#ifndef INT_MAX
#define INT_MAX 2147483647
#endif

#ifndef INT_MIN
#define INT_MIN (-INT_MAX - 1)
#endif

#ifndef UINT_MAX
#define UINT_MAX 4294967295U
#endif

#ifndef LONG_MAX
#define LONG_MAX __LONG_MAX__
#endif

#ifndef LONG_MIN
#define LONG_MIN (-LONG_MAX - 1L)
#endif

#ifndef ULONG_MAX
#define ULONG_MAX (2UL * __LONG_MAX__ + 1UL)
#endif

#ifndef LLONG_MAX
#define LLONG_MAX 9223372036854775807LL
#endif

#ifndef LLONG_MIN
#define LLONG_MIN (-LLONG_MAX - 1LL)
#endif

#ifndef ULLONG_MAX
#define ULLONG_MAX 18446744073709551615ULL
#endif

#endif
