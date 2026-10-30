#include <errno.h>
#include <unistd.h>

#ifdef PCC_OWNED_ERRNO
int *pcc_errno_location(void);
int pcc_errno_get(void);
void pcc_errno_set(int);
#endif

int main(void) {
    int *cell = &errno;
    char byte = 0;
    errno = 123;
    if (read(-1, &byte, 1) != -1 || errno != EBADF) return 10;
    if (&errno != cell) return 11;
#ifdef PCC_OWNED_ERRNO
    if (cell != pcc_errno_location() || pcc_errno_get() != EBADF) return 12;
    errno = 37;
    if (pcc_errno_get() != 37) return 13;
    pcc_errno_set(123);
    if (errno != 123 || &errno != cell) return 14;
#endif
    return 0;
}
