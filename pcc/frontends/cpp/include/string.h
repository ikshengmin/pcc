// PCC-owned C++17 <string.h>, N4659 [depr.c.headers], [cstring.syn].
// The shared <cstring> already declares all standard names globally and
// imports those canonical C/C++ entities into std. Keep const-correct search
// overloads; never use the C compatibility/fake-libc string.h instead.
#ifndef _PCC_CXX_STRING_H
#define _PCC_CXX_STRING_H
#include "cstring"
#endif
