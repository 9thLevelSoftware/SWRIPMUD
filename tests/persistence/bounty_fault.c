/* Fault injection for a disposable bounty-recovery test process. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <unistd.h>

static void read_control(char *control, size_t capacity)
{
    int fd = open("../system/bounty-test-stop", O_RDONLY);
    if (fd < 0)
        return;
    ssize_t count = read(fd, control, capacity - 1);
    if (count > 0)
        control[count] = '\0';
    close(fd);
}

static const char *basename_of(const char *path)
{
    const char *slash = strrchr(path, '/');
    return slash ? slash + 1 : path;
}

static void record_trigger(const char *control)
{
    int fd = open("../system/bounty-test-triggered", O_CREAT | O_EXCL | O_WRONLY, 0600);
    if (fd < 0)
        _exit(89);
    size_t length = strlen(control);
    if (write(fd, control, length) != (ssize_t)length || close(fd) != 0)
        _exit(89);
}

int rename(const char *from, const char *to)
{
    int (*real_rename)(const char *, const char *) = dlsym(RTLD_NEXT, "rename");
    if (!real_rename)
        _exit(89);
    char control[64] = {0};
    read_control(control, sizeof(control));
    const char *name = basename_of(to);
    int player_save = strncmp(to, "../player/", 10) == 0;
    if (player_save && strncmp(control, "failplayer:", 11) == 0 &&
        strcmp(name, control + 11) == 0)
    {
        record_trigger(control);
        errno = EIO;
        return -1;
    }

    int result = real_rename(from, to);
    if (result == 0 &&
        ((strcmp(control, "intent") == 0 && strcmp(name, "bounty-transaction") == 0) ||
         (strcmp(control, "list") == 0 && strcmp(name, "disintigration.lst") == 0) ||
         (player_save && strncmp(control, "player:", 7) == 0 && strcmp(name, control + 7) == 0)))
    {
        record_trigger(control);
        _exit(88);
    }
    return result;
}

int unlink(const char *path)
{
    int (*real_unlink)(const char *) = dlsym(RTLD_NEXT, "unlink");
    if (!real_unlink)
        _exit(89);
    int result = real_unlink(path);
    if (result == 0 && strcmp(basename_of(path), "bounty-transaction") == 0)
    {
        char control[64] = {0};
        read_control(control, sizeof(control));
        if (strcmp(control, "clear") == 0)
        {
            record_trigger(control);
            _exit(88);
        }
    }
    return result;
}
