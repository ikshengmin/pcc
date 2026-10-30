#ifndef _FAKE_TERMIO_H
#define _FAKE_TERMIO_H

/* The legacy Linux ioctl ABI is not a POSIX or Darwin interface. */
#if !defined(__linux__) || (!defined(__x86_64__) && !defined(__aarch64__))
#error termio.h: unsupported target ABI
#endif

#include <termios.h>
#include <sys/ioctl.h>

#endif
