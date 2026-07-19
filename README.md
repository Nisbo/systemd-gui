# Systemd Gui

Systemd Gui is a small web interface for managing systemd `.service` units on
Debian-style servers. It is intended for users who prefer a browser UI over
working with SSH, `nano`, `vi`, `systemctl` and `journalctl`.

The app is written in Python with Flask and is installed behind nginx and
Gunicorn.

## Features

- List `.service` units with status and autostart state.
- Filter and search services.
- Mark favorite services.
- Start, stop, restart and reload services.
- Enable and disable autostart when systemd supports it.
- View unit files and drop-ins.
- Create and edit drop-in overrides without changing package-owned unit files.
- View service logs from `journalctl`.
- Open logs in a separate live-view window.
- Search loaded log lines.
- Edit editable unit files below `/etc/systemd/system`.
- Create, restore, delete and download unit backups.
- Store per-service notes.
- Show curated beginner-friendly service information.
- Run `systemctl daemon-reload`.
- Change the web login password.
- Update from release ZIP, uploaded ZIP or git branch.
- Create, restore and delete app update backups.
- Manage a local Quick Shell command menu for the `qs` helper.

## Safety

Systemd Gui can control system services and should be treated as an
administrative tool. Keep it on a private network or behind your own access
controls.

The app intentionally limits the first release to `.service` units. Protected
services such as `ssh`, `networking` and `systemd-*` are blocked by default to
reduce the risk of locking yourself out of a server.

Direct unit editing is limited to real unit files below `/etc/systemd/system`.
Vendor units should be changed through proper overrides or drop-ins instead of
editing package-owned files directly.

## Ports

The Debian installer uses:

- Public nginx port: `8850`
- Internal Gunicorn bind: `127.0.0.1:8851`

These can be overridden through environment variables before running the
installer.

## Install On Debian 12

Run as root:

```bash
cd /opt
git clone https://github.com/Nisbo/systemd-gui.git systemd-gui
cd /opt/systemd-gui
./scripts/install_debian.sh
```

At the end, the installer prints the generated login password.

Open:

```text
http://YOUR-SERVER-IP:8850
```

## Installer Environment Variables

You can override defaults before running the installer:

```bash
export SYSTEMD_GUI_PUBLIC_PORT=8850
export SYSTEMD_GUI_HOST=127.0.0.1
export SYSTEMD_GUI_PORT=8851
export SYSTEMD_GUI_PASSWORD='change-me'
./scripts/install_debian.sh
```

The installer writes `/etc/systemd-gui.env`, creates the
`systemd-gui.service` systemd unit, configures nginx and starts the app.

## Updates And Backups

The Settings page includes update actions and app update backups.

Before replacing app files, Systemd Gui creates an app backup under:

```text
data/app-updates/backups
```

App backups include the application files plus selected runtime data such as
favorites, service notes, Quick Shell entries, unit backups and environment-file
backups. The app backup directory itself is not copied recursively.

## Quick Shell

The installer creates a local shell helper:

```bash
qs
```

Quick Shell entries are managed in the web UI under **Quick Shell** and stored
in:

```text
data/quick-shell.json
```

Entries can be nested into categories and subcategories. Disabled entries stay
stored in the web UI but are hidden from the `qs` menu. Commands are not run
from the browser; the web UI only manages the list, and execution happens in
the local server shell. By default, `qs` exits after a command runs. Enable
**Show menu after command** on individual commands when you want the menu to
open again afterward.

Simple directory commands such as `cd`, `cd /opt` and `cd ~/project` are treated
specially: `qs` opens your shell in that directory. More complex shell commands
run normally in a subprocess.

Fresh installations create `/usr/local/bin/qs` automatically. If you added Quick
Shell through a Git update, open **Quick Shell** in the web UI and use **Install
or update helper** once.

## Local Development

```bash
python3 run.py
```

On non-Linux systems or systems without `systemctl`, the app will load but
service actions and live service data will be unavailable.

## License

MIT License. See [LICENSE](LICENSE).
