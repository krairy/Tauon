# Tauon Mirror

Tauon on your PC follows whatever your phone plays through Jellyfin: same song,
same position, pause and skip. Tauon is muted while it follows, so you hear
only the phone, and gets its volume back when you stop the mirror.

## Setup on Windows

1. Install **MSYS2** from https://www.msys2.org/ and keep the suggested folder
   `C:\msys64`. Tauon is built from this folder with it.
   (Don't want to build? Install the regular Tauon from
   https://tauonmusicbox.rocks/ or run `winget install Taiko2k.TauonMusicBox`,
   then start with `-UseInstalled`.)
2. Install **Git** from https://git-scm.com/download/win if this folder came
   without the `src\phazor\miniaudio` and `src\phazor\kissfft` parts.
3. In Tauon, sign in to your Jellyfin server (Settings > Connect > Services > Jellyfin).
   The mirror uses the same login.
4. Double-click **`tauon-mirror.cmd`** in this folder.

The first run downloads and builds a lot (often 15-30 minutes). Later runs
take a few seconds. Leave the window open: closing it, or pressing Ctrl+C,
stops the mirror and gives Tauon its volume back.

To start everything at login, run once from a terminal in this folder:

    .\tauon-mirror.cmd -Device moto -InstallStartup

Remove it again with `.\tauon-mirror.cmd -RemoveStartup`.

## What the script does

1. Picks Tauon: built from this folder (needs MSYS2), otherwise an installed
   Tauon. It installs the MSYS2 packages, creates `.venv`, installs the Python
   packages and builds Tauon, skipping each step that is already done
   (`build-tauon.sh`).
2. Turns on Tauon's remote control (`enable-remote-interface` in
   `tauon.conf`). It only changes that file while Tauon is closed, because
   Tauon rewrites it when it exits.
3. Starts Tauon and waits until `http://127.0.0.1:7814/api1/version` answers.
4. Starts `tauon_mirror.py`, which watches Jellyfin and steers Tauon.

## Options

Pass these to `tauon-mirror.cmd` or `tauon-mirror.ps1`:

| Option | Meaning |
| --- | --- |
| `-Device NAME` | Follow only this Jellyfin device or app (part of its name, e.g. `moto`, `Finamp`). Env var: `TAUON_MIRROR_DEVICE` |
| `-Tolerance SECONDS` | How far Tauon may drift before it is re-synced (default 2.5) |
| `-JellyfinUrl URL` | Jellyfin server, e.g. `http://my-server:8096`. Env var: `JELLYFIN_URL` |
| `-JellyfinApiKey KEY` | Jellyfin API key. Env var: `JELLYFIN_API_KEY` |
| `-NoMute` | Leave Tauon's volume alone |
| `-UseInstalled` | Prefer an installed Tauon over building this folder |
| `-TauonExe PATH` | Use this Tauon program |
| `-TauonConf PATH` | Use this `tauon.conf` |
| `-SkipBuild` | Don't build or update, use what is there |
| `-Rebuild` | Reinstall the Python packages and rebuild Tauon |
| `-Msys2Root PATH` | MSYS2 folder if it is not `C:\msys64`. Env var: `MSYS2_ROOT` |
| `-Python PATH` | Python that runs the mirror |
| `-TauonTimeout SECONDS` | How long to wait for Tauon to start (default 120) |
| `-InstallStartup` / `-RemoveStartup` | Add or remove the login shortcut |
| `-NoPrompt` | Never ask questions |
| `-Verbose` | Show more detail |

Without `-JellyfinUrl` and `-JellyfinApiKey` the mirror uses the Jellyfin
login saved in Tauon. The API key is never put into the login shortcut; the
script offers to save it as a user environment variable instead.

## Troubleshooting

- **"Tauon is open, but its remote control is off"**: close Tauon completely
  (if it sits in the system tray, right-click the icon and choose Exit). The
  script waits, turns remote control on and starts Tauon again.
- **Windows firewall asks about Tauon**: either answer works; the mirror talks
  to Tauon on the same PC. Tauon's remote control listens on port 7814 for
  the whole network, so choose "Cancel"/private networks only if you don't
  want other devices to control Tauon.
- **"You need MSYS2"**: install it (step 1) or use `-UseInstalled`.
- **Building fails**: run again; after an MSYS2 update it often works. If
  installing MSYS2 packages keeps failing, the reason is on the lines starting
  with `error:`, also kept in `build\windows-mirror\pacman.log` in the Tauon
  folder (e.g. `C:\Tauon\build\windows-mirror\pacman.log`). Installing needs
  about 6 GB free on the MSYS2 drive.
  Otherwise open "MSYS2 CLANG64" from the Start menu, run `pacman -Syu`, then
  try `tauon-mirror.cmd -Rebuild`.
- **"SDL3.dll / libpython3.x.dll / libc++.dll was not found"**: these come from
  MSYS2 (`C:\msys64\clang64\bin`), so `.venv\bin\tauon.exe` can't be
  started by double-clicking it. Start Tauon with `tauon-mirror.cmd`, or with
  **`start-tauon.cmd`** for Tauon alone. Both check for these files and
  reinstall what is missing. If MSYS2 is not in `C:\msys64`, set `MSYS2_ROOT`.
- **"No Python 3.9 or newer"** (installed Tauon only): install Python from
  https://www.python.org/downloads/windows/ and tick "Add python.exe to PATH".
- **"No Jellyfin login"**: sign in to Jellyfin in Tauon, or pass
  `-JellyfinUrl` and `-JellyfinApiKey`.
- **Logs**: `%LOCALAPPDATA%\tauon-mirror\tauon-mirror.log`. Saved state is
  next to it in `state.json`.
- **Play counts and scrobbling**: Tauon counts and scrobbles what it plays,
  even muted. Turn scrobbling off in Tauon if your phone scrobbles already.

## Linux and macOS

Only the mirror itself is needed (Python 3.9+, no extra packages). Turn on
Settings > Connect > Sharing > Enable remote control in Tauon, then:

    python3 tauon_mirror.py --device moto

`tauon.conf` is found automatically (also for Flatpak and for a source
checkout's `user-data`); override it with `--tauon-conf PATH` or the
`TAUON_CONF` variable. Run `python3 tauon_mirror.py --help` for all options.

## Tests

    cd extra/windows/mirror
    python3 -m unittest -v test_tauon_mirror
