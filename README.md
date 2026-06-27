# systemd-gui

A small web GUI for managing systemd `.service` units on Debian-style servers.
It is designed for beginners who prefer a web interface over editing unit files
with `nano` or `vi` over SSH.

## Status

Early development prototype.

## Planned / Initial Features

- list all `.service` units
- show active/inactive/failed status
- show enabled/disabled autostart state
- start, stop, restart and reload services
- enable and disable services
- show unit file content
- show drop-ins
- show logs with `journalctl`
- search and filter services
- favorites
- edit unit files with backups
- run `systemctl daemon-reload`
- safety: only `.service` units in the first version
- safety: protected services such as `ssh`, `networking` and `systemd-*` are blocked by default

## Ports

The installer uses nginx as public reverse proxy on port `8850` and Gunicorn
internally on `127.0.0.1:8851`.

## Quick Install On Debian 12

Run as root:

```bash
cd /opt
git clone https://github.com/Nisbo/systemd-gui.git systemd-gui
cd /opt/systemd-gui
./scripts/install_debian.sh
```

The installer prints the login password at the end.

## Security Notes

This app can control system services and is therefore powerful. Keep it private,
keep login enabled, and do not expose it publicly without HTTPS and additional
access controls.

The first version intentionally blocks protected services by default and edits
only unit files below `/etc/systemd/system`.
