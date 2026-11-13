#ifndef _FAKE_SIGNAL_H
#define _FAKE_SIGNAL_H

#include "_fake_defines.h"
#include "_fake_typedefs.h"

#ifndef SIG_BLOCK
#if defined(__linux__)
#define SIG_BLOCK 0
#else
#define SIG_BLOCK 1
#endif
#endif

#ifndef SIG_UNBLOCK
#if defined(__linux__)
#define SIG_UNBLOCK 1
#else
#define SIG_UNBLOCK 2
#endif
#endif

#ifndef SIG_SETMASK
#if defined(__linux__)
#define SIG_SETMASK 2
#else
#define SIG_SETMASK 3
#endif
#endif

#ifndef SIGHUP
#define SIGHUP 1
#endif

#ifndef SIGINT
#define SIGINT 2
#endif

#ifndef SIGQUIT
#define SIGQUIT 3
#endif

#ifndef SIGILL
#define SIGILL 4
#endif

#if !defined(_WIN32)
#ifndef SIGTRAP
#define SIGTRAP 5
#endif
#endif

#ifndef SIGABRT
#if defined(_WIN32)
#define SIGABRT 22
#else
#define SIGABRT 6
#endif
#endif

#if !defined(_WIN32)
#ifndef SIGBUS
#if defined(__linux__)
#define SIGBUS 7
#else
#define SIGBUS 10
#endif
#endif
#endif

#ifndef SIGFPE
#define SIGFPE 8
#endif

#if !defined(_WIN32)
#ifndef SIGKILL
#define SIGKILL 9
#endif

#ifndef SIGUSR1
#if defined(__linux__)
#define SIGUSR1 10
#else
#define SIGUSR1 30
#endif
#endif

#ifndef SIGUSR2
#if defined(__linux__)
#define SIGUSR2 12
#else
#define SIGUSR2 31
#endif
#endif

#ifndef SIGPIPE
#define SIGPIPE 13
#endif

#ifndef SIGCHLD
#if defined(__linux__)
#define SIGCHLD 17
#else
#define SIGCHLD 20
#endif
#endif
#endif

#ifndef SIGSEGV
#define SIGSEGV 11
#endif

#ifndef SIGALRM
#define SIGALRM 14
#endif

#ifndef SIGTERM
#define SIGTERM 15
#endif

#ifndef SIGTSTP
#if defined(__linux__)
#define SIGTSTP 20
#else
#define SIGTSTP 18
#endif
#endif

#if defined(_WIN32)
#ifndef SIGBREAK
#define SIGBREAK 21
#endif
#endif

#ifndef SIGTTIN
#define SIGTTIN 21
#endif

#ifndef SIGTTOU
#define SIGTTOU 22
#endif

#ifndef SIGWINCH
#define SIGWINCH 28
#endif

#ifndef SIG_DFL
#define SIG_DFL ((void (*)(int))0)
#endif

#ifndef SIG_IGN
#define SIG_IGN ((void (*)(int))1)
#endif

#ifndef SIG_ERR
#define SIG_ERR ((void (*)(int))-1)
#endif

struct sigaction {
    void (*sa_handler)(int);
    sigset_t sa_mask;
    int sa_flags;
#if defined(__linux__) || defined(_WIN32)
    void (*sa_restorer)(void);
#endif
};

int sigaddset(sigset_t *set, int signo);
int sigdelset(sigset_t *set, int signo);
int sigemptyset(sigset_t *set);
int sigfillset(sigset_t *set);
int sigismember(const sigset_t *set, int signo);
int sigprocmask(int how, const sigset_t *set, sigset_t *oldset);
int sigaction(int signum, const struct sigaction *act, struct sigaction *oldact);
int sigsuspend(const sigset_t *sigmask);
int kill(pid_t pid, int sig);
void (*signal(int sig, void (*func)(int)))(int);

#endif
