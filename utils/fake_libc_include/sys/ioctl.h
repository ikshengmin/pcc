#ifndef _FAKE_SYS_IOCTL_H
#define _FAKE_SYS_IOCTL_H

#include "_fake_defines.h"
#include "_fake_typedefs.h"

#if defined(__linux__) && (defined(__x86_64__) || defined(__aarch64__))
/* Linux asm-generic legacy terminal ioctl ABI (also used by x86-64). */
#define NCC 8
struct termio {
    unsigned short c_iflag;
    unsigned short c_oflag;
    unsigned short c_cflag;
    unsigned short c_lflag;
    unsigned char c_line;
    unsigned char c_cc[NCC];
};

#define TCGETA 0x5405
#define TCSETA 0x5406
#define TCSETAW 0x5407
#define TCSETAF 0x5408
#define TCXONC 0x540a
#endif

int ioctl(int fd, unsigned long request, ...);

#endif
