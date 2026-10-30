#ifndef _FAKE_TERMIOS_H
#define _FAKE_TERMIOS_H

#include "_fake_defines.h"
#include "_fake_typedefs.h"

#if defined(__linux__)
/* Linux libc ABI, not the smaller kernel TCGETS struct termios. */
#if !defined(__x86_64__) && !defined(__aarch64__)
#error termios.h: unsupported Linux target ABI
#endif

typedef unsigned int tcflag_t;
typedef unsigned char cc_t;
typedef unsigned int speed_t;

#define NCCS 32
struct termios {
    tcflag_t c_iflag;
    tcflag_t c_oflag;
    tcflag_t c_cflag;
    tcflag_t c_lflag;
    cc_t c_line;
    cc_t c_cc[NCCS];
    speed_t c_ispeed;
    speed_t c_ospeed;
};

#define VINTR 0
#define VQUIT 1
#define VERASE 2
#define VKILL 3
#define VEOF 4
#define VTIME 5
#define VMIN 6
#define VSWTC 7
#define VSTART 8
#define VSTOP 9
#define VSUSP 10
#define VEOL 11
#define VREPRINT 12
#define VDISCARD 13
#define VWERASE 14
#define VLNEXT 15
#define VEOL2 16

/* Terminal mode bits shared by the termio and termios interfaces. */
#define INPCK 0x0010
#define ISTRIP 0x0020
#define INLCR 0x0040
#define ICRNL 0x0100
#define IXON 0x0400
#define IXANY 0x0800
#define OPOST 0x0001
#define ONLCR 0x0004
#define CSIZE 0x0030
#define CS8 0x0030
#define ISIG 0x0001
#define ICANON 0x0002
#define ECHO 0x0008
#define ECHOCTL 0x0200
#define FLUSHO 0x1000

#ifndef _POSIX_VDISABLE
#define _POSIX_VDISABLE 0
#endif

#define TCSANOW 0
#define TCSADRAIN 1
#define TCOOFF 0
#define TCOON 1

#else
typedef unsigned int tcflag_t;
typedef unsigned char cc_t;
typedef unsigned long speed_t;

#ifndef NCCS
#define NCCS 20
#endif

struct termios {
    tcflag_t c_iflag;
    tcflag_t c_oflag;
    tcflag_t c_cflag;
    tcflag_t c_lflag;
    cc_t c_cc[NCCS];
    speed_t c_ispeed;
    speed_t c_ospeed;
};

#define VINTR 0
#define VQUIT 1
#define VERASE 2
#define VKILL 3
#define VEOF 4
#define VEOL 5
#define VEOL2 6
#define VWERASE 7
#define VREPRINT 8
#define VSUSP 9
#define VDSUSP 10
#define VSTART 11
#define VSTOP 12
#define VLNEXT 13
#define VDISCARD 14
#define VSTATUS 15
#define VMIN 16
#define VTIME 17

#ifndef _POSIX_VDISABLE
#define _POSIX_VDISABLE 0xff
#endif

#define TCSANOW 0
#define TCSADRAIN 1
#define TCOOFF 0
#define TCOON 1

#endif

int tcdrain(int fd);
int tcflow(int fd, int action);
int tcgetattr(int fd, struct termios *termios_p);
int tcsetattr(int fd, int optional_actions, const struct termios *termios_p);

#endif
