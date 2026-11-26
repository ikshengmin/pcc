// PCC-owned C++17 <stdlib.h>, N4659 [depr.c.headers], [cstdlib.syn].
// Reuse the same C ABI declarations and macros as <cstdlib>. The C++
// overloads that <cstdlib> declares only in std must also be visible to
// unqualified/global lookup through this spelling. No missing runtime
// implementation or unsupported value/callback ABI is replaced here.
#ifndef _PCC_CXX_STDLIB_H
#define _PCC_CXX_STDLIB_H
#include "cstdlib"
using std::atexit;
using std::at_quick_exit;
using std::bsearch;
using std::qsort;
using std::abs;
using std::div;
#endif
