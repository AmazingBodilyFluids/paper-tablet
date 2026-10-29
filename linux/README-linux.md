# Paper Tablet on Linux (preview)

Status: **first build, not yet tested on a real Linux desktop.** It is written for Wayland
compositors that use libinput (Hyprland, Sway, GNOME, KDE), and X11 should work too. The
first target is Omarchy (Arch + Hyprland).

## Install

```sh
git clone -b linux https://github.com/AmazingBodilyFluids/paper-tablet.git
cd paper-tablet
./linux/setup.sh
```

`setup.sh` does four things, and asks for `sudo` where needed:
1. On Arch, it installs `python-gobject` and `webkit2gtk-4.1` (the window). On other distros it prints the matching
   packages for you to install.
2. It creates a private Python environment in `~/.local/share/paper-tablet/venv` with paramiko and pywebview.
3. It adds a udev rule so your user may create virtual input devices (`/dev/uinput`, the same rule Steam
   uses for controllers), and loads the `uinput` module at boot.
4. It adds a "Paper Tablet" entry to your app launcher.

Log out and back in once afterwards. Then start Paper Tablet from the launcher, or run
`linux/paper-tablet.sh`. The setup guide in the app does the rest, just as on Windows.

## How it works on Linux

- **Pen.** The app creates a virtual pen tablet ("Paper Tablet Pen") through uinput. Your desktop sees it
  like a USB tablet, with hover, pressure, tilt and the eraser end. Krita, GIMP, Inkscape, Xournal++ and
  other apps get it through the normal tablet support.
- **Shortcuts, pan and rotate.** These go through a virtual keyboard and an absolute mouse ("Paper Tablet
  Keys" / "Paper Tablet Mouse").
- **Monitors and the focused app** come from `hyprctl` on Hyprland, and from `xrandr` elsewhere. Hold-to-snap
  uses the focused window's app name (for example `krita`, `gimp`) for its allow-list.
- **"Windows touch" works on Linux too.** That profile setting creates a virtual touchscreen ("Paper Tablet
  Touch"), so apps scroll with inertia and pinch-zoom by themselves. With more than one monitor, Hyprland binds a
  touchscreen to a single output; if touches land on the wrong screen, set `input:touchdevice:output` to the
  monitor you chose on the *Screen area* page.
- **There is no splash screen and no tray icon on Linux.**

## Things to check if something's off

- **"No access to /dev/uinput".** Run `setup.sh` again, log out and in, then check `ls -l /dev/uinput`.
  It should show a `+` (an ACL for your user).
- **The tablet isn't found.** The tablet's USB network must give your PC an address in `10.11.99.x`. Check
  `ip -4 addr | grep 10.11.99`. NetworkManager does this by itself. With plain systemd-networkd, add
  `/etc/systemd/network/50-remarkable.network` containing

  ```ini
  [Match]
  Driver=rndis_host cdc_ether cdc_ncm

  [Network]
  DHCP=ipv4
  ```

  then run `sudo systemctl restart systemd-networkd`.
- **The pen lands in the wrong place (Hyprland).** The app maps the tablet across all monitors itself, so
  leave Hyprland's `input:tablet:output` and `region_*` options unset. Choose the display on the app's
  *Screen area* page instead.
- **Krita doesn't react to the pen (Wayland).** Krita's native Wayland mode can ignore a virtual tablet.
  Start it through XWayland instead: `QT_QPA_PLATFORM=xcb krita`. Other Qt drawing apps may need the same.
- **The rotate gesture.** Rotate needs the app's own rotate-view key, set on *Panel & gestures*:
  `shift+space` for Krita, `r` for apps that use R.

Please report what works and what doesn't. The logs are in `~/.config/PaperTablet/`, and in the terminal
if you start it with `linux/paper-tablet.sh`.
