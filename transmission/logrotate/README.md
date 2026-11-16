Syslog rotation
===============

Install `syslog` as `/etc/logrotate.d/syslog` with mode 0644, owned by root.
Remove `/var/log/syslog` from `/etc/logrotate.d/rsyslog` first so that only
one rule manages the file. Keep the other rsyslog log paths and settings.

The rule rotates syslog daily, retains seven rotations, and compresses all
but the most recent rotation. The postrotate hook tells rsyslog to reopen
its files. Missing or empty logs are skipped.

Validate with `logrotate --debug /etc/logrotate.conf`. The existing
`logrotate.timer` runs the rotation automatically.
