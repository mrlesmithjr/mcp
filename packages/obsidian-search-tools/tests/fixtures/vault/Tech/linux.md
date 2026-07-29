---
tags: [tech, linux, sysadmin, shell]
---
# Linux System Administration

## Process Management

The Linux kernel schedules processes and threads across CPU cores using the Completely Fair
Scheduler. Each process has a PID and belongs to a process group and session. Use ps aux to
list running processes. The top and htop utilities display real-time CPU, memory, and process
information. Kill sends signals to processes: SIGTERM requests graceful shutdown; SIGKILL forces
immediate termination.

## File System and Permissions

Linux uses a hierarchical filesystem rooted at /. File permissions are expressed as a 9-bit mask
split into owner, group, and other triplets of read, write, and execute bits. Chmod sets
permissions numerically (755) or symbolically (u+x). Chown changes ownership. Setuid and setgid
bits allow a program to execute with the file owner's privileges.

## Shell Scripting

Bash scripts automate repetitive administration tasks. Variables are assigned without spaces:
FOO=bar. Use $FOO or ${FOO} to expand variables. Command substitution captures output: DATE=$(date +%Y-%m-%d).
Pipelines chain commands: grep ERROR /var/log/syslog | sort | uniq -c | sort -rn.
Conditionals use if/then/fi blocks with test expressions.

## Package Management

Debian-based systems use apt to install, upgrade, and remove packages. Red Hat-based systems
use dnf or yum. Snap and Flatpak provide cross-distribution packaging with sandboxed runtimes.
Always update the package index (apt update) before installing to get current versions.

## System Services

Systemd is the init system on modern Linux distributions. Systemctl manages services: start,
stop, restart, enable (start at boot), and status. Journalctl queries the systemd journal for
logs. Unit files in /etc/systemd/system/ define custom services.
