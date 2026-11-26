// PCC-owned C++17 assertion macro, N4659 [assertions.assert], C11 7.2.
// Repeated inclusion must reflect the NDEBUG state at that inclusion.
#undef assert
#ifdef NDEBUG
#define assert(expression) ((void)0)
#else
#define assert(expression) ((expression) ? (void)0 : __pcc_cpp_assert_fail(#expression, __FILE__, __LINE__))
#endif
