# Tauon + Jellyfin + Finamp on Windows: the easy setup guide

## What you'll end up with

Your music lives in one place, a Windows PC running **Jellyfin**. **Tauon** plays it on that PC (or any other computer), and **Finamp** plays it on your phone. A small helper script, tauon-mirror, makes Tauon follow the phone: same song, same spot, same pause, usually within about a second. The phone plays the sound and Tauon becomes the big now-playing screen.

- **Jellyfin** is the server. It holds the library and remembers favourites, playlists and what you played.
- **Tauon** is the desktop player. It streams from Jellyfin instead of reading the files directly.
- **Finamp** is the phone app (Android and iPhone). It streams from Jellyfin and can download songs for offline listening.

The PC has to be switched on for the phone to stream from it, except for songs Finamp has already downloaded.

## What you need

Set aside about 30 minutes, plus however long Jellyfin takes to scan your music.

- [ ] A Windows 10 or 11 PC that holds your music files (MP3, FLAC and so on), in one folder or a few
- [ ] Your phone, connected to the **same Wi-Fi** as the PC
- [ ] An administrator account on the PC (needed to install programs)
- [ ] **MSYS2** from [msys2.org](https://www.msys2.org/), installed to the suggested folder `C:\msys64`. Tauon is built with it, and it brings its own Python.
- [ ] **Git** from [git-scm.com/download/win](https://git-scm.com/download/win), to download Tauon's code. The installer's default options are fine.
- [ ] The file **tauon_mirror.py** (the helper script) and its starter **tauon-mirror.cmd**. Both come with Tauon's code in Step 0, in the folder `extra\windows\mirror`.
- [ ] A username and password you'll remember for Jellyfin. Write them down, because you'll type them into Tauon and Finamp too.

## Step 0: Download and build Tauon

This Tauon comes from a fork on GitHub with the mirror and a ready-made layout built in. You build it once, which takes 15 to 30 minutes; after that it starts in seconds.

1. Press **Windows key + R**, type `cmd`, and press **Enter**.
2. In the black window, paste this line and press **Enter**. It downloads Tauon into `C:\Tauon`:

   ```
   git clone --recurse-submodules -b feature/windows-mirror https://github.com/krairy/Tauon.git C:\Tauon
   ```

3. Open the folder `C:\Tauon\extra\windows\mirror` in File Explorer and double-click **tauon-mirror.cmd**.
4. A blue-and-black window shows the build steps. When it finishes, Tauon opens. The window then says there is no Jellyfin login yet: that's expected at this point. Close the window.

From now on, start Tauon by double-clicking **tauon-mirror.cmd**. It updates the build if needed, switches on Tauon's remote control, opens Tauon and starts the mirror.

## Step 1: Install Jellyfin on the PC

You install Jellyfin once, and from then on it runs in the background.

1. Go to [jellyfin.org/downloads/server](https://jellyfin.org/downloads/server), choose **Windows**, and download the **x64 installer** (the `.exe`).
2. Run the installer and click **Next** through it. When it asks how to run Jellyfin:
   - Pick **Install as a Service** if you want Jellyfin to start with Windows, even before anyone logs in. This is the best choice for an always-on PC.
   - Pick the basic install if you'd rather start it yourself. It runs from a tray icon near the clock.
3. When the installer finishes, open your browser and go to **http://localhost:8096**. The Jellyfin setup wizard appears.
4. Pick your language, then create your user: type the **username and password** you wrote down. This first user is the admin.
5. Leave the library page for now (Step 2 covers it) and click **Next**.
6. On **Remote Access**, leave **Allow remote connections to this server** ticked. Without it, your phone can't connect.
7. Click **Finish** and log in with your new username and password.

If **http://localhost:8096** doesn't load, wait a minute and refresh. Jellyfin takes a moment to start the first time.

## Step 2: Add your music to Jellyfin

You tell Jellyfin where your music folder is, and it builds the library from it.

1. In Jellyfin (http://localhost:8096), click the **menu icon** (three lines, top left), then **Dashboard**, then **Libraries**.
2. Click **Add Media Library**.
3. Set **Content type** to **Music** and type a display name, for example *Music*.
4. Next to **Folders**, click **+** and browse to your music folder, for example `C:\Users\You\Music` or `D:\Music`. Click **OK**. Repeat for each music folder.
5. Click **OK** to save the library. The scan starts on its own.
6. Wait for the scan to finish: the progress bar on **Dashboard** goes away. A big collection can take 10 to 30 minutes.
7. Go back to the home page. You should see your albums. If they're there, Jellyfin is ready.

When you add new music to the folder later, Jellyfin picks it up within a few minutes. You can also click **Scan All Libraries** on the Dashboard to do it right away.

## Step 3: Find the PC's address and open the firewall

Your phone needs the PC's address on your home network, something like **http://192.168.1.23:8096**.

**Find the address**

1. Press **Windows key + R**, type `cmd`, and press **Enter**.
2. In the black window, type `ipconfig` and press **Enter**.
3. Look for **IPv4 Address** under your Wi-Fi or Ethernet adapter, for example `192.168.1.23`.
4. Your server address is that number with `http://` in front and `:8096` after it: **http://192.168.1.23:8096**. Write it down.

**Make sure Windows lets the phone in**

1. Open **Settings > Network & internet**, click your Wi-Fi or Ethernet connection, and set **Network profile type** to **Private**. Windows blocks most incoming connections on Public networks.
2. If Windows showed an **Allow access** pop-up for Jellyfin, you should have ticked **Private networks**. If you clicked Cancel, search the Start menu for **Allow an app through Windows Firewall**, click **Change settings**, find **Jellyfin**, and tick **Private**.
3. Test it: on your phone's browser, open **http://192.168.1.23:8096** (your own number). If the Jellyfin login page appears, it works.

The address can change when your router restarts. If the phone suddenly can't connect, run `ipconfig` again. To stop this from happening, set a fixed ("reserved") address for the PC in your router's settings.

## Step 4: Connect Tauon

Tauon logs in to Jellyfin and copies the track list (not the files) into a playlist called **Jellyfin Collection**.

1. Open Tauon, open the main menu, and click **Settings**.
2. Go to **Connect**, then **Jellyfin**.
3. Fill in the three fields:
   - **Username** and **Password**: the ones you created in Step 1.
   - **Server URL**: `http://localhost:8096` if Tauon runs on the same PC as Jellyfin. On another computer, use the address from Step 3, for example `http://192.168.1.23:8096`.
4. Click **Test connectivity**. A success message means the login works.
5. Click **Import music**. After a short wait, a new playlist tab called **Jellyfin Collection** appears with your whole library.
6. If you already have playlists in Jellyfin (for example made in Finamp), click **Import playlists**. Each one appears as its own tab in Tauon.

Turn off **Allow local scrobbling** here. With the mirror running, the phone already reports every song, so leaving it on counts plays twice.

Play songs from the **Jellyfin Collection** tab, not from Tauon's own folder import. Only those tracks sync with Jellyfin.

## Step 5: Connect Finamp on your phone

Finamp signs in to the same Jellyfin account, so it sees the same music, hearts and playlists.

1. Install **Finamp** from the Google Play Store (Android) or the App Store (iPhone). It's free.
2. Open it. When it asks for a server, type the address from Step 3, for example `http://192.168.1.23:8096`. Type the `http://` and the `:8096` too.
3. Log in with the **same username and password** you used in Tauon.
4. Choose your **Music** library when Finamp asks which one to use.
5. Your albums appear. Tap one and play a song to check it works.

**For listening away from home:** long-press an album or playlist and choose **Download**. Downloaded songs play without the PC or Wi-Fi.

## Step 6: Make Tauon follow your phone

tauon-mirror asks Jellyfin every half second what the phone is playing and tells Tauon to do the same. It runs on the PC where Tauon runs.

```
phone (Finamp) ──reports playback──▶ Jellyfin ◀──asks every 0.5 s── tauon_mirror.py ──remote control──▶ Tauon
```

**6a. Make Finamp report more often**

Finamp reports track changes, pauses and seeks right away, but by default it sends its position only every 150 seconds. In Finamp, open **Settings > Playback Reporting & Play On** and set **Playback Session Update Frequency** to **10** (seconds). Tauon then catches up faster if it drifts.

**6b. Start it**

1. Close Tauon completely. If it sits in the system tray near the clock, right-click its icon and choose **Exit**.
2. Double-click **tauon-mirror.cmd** in `C:\Tauon\extra\windows\mirror`. It switches on Tauon's remote control, opens Tauon and starts the mirror. It uses the Jellyfin login you entered in Tauon in Step 4, so there's no key to copy.
3. If Windows asks whether Tauon may use the network, either answer works: the mirror talks to Tauon on the same PC.
4. Play a song in Finamp. Within a few seconds Tauon jumps to the same song and position, with its volume at 0. Pause on the phone, and Tauon pauses too.
5. To stop the mirror, close the window or press **Ctrl+C** in it. Tauon gets its volume back.

**How close it stays:** song changes and pauses show up in Tauon in about 1 second. If Tauon's position drifts more than 2.5 seconds from the phone, the script jumps it back. For a tighter match, start it with `-Tolerance 0.5` (see Step 7 for how to type options). Corrections then happen more often, but at most one every 5 seconds.

## Step 7: Start the mirror with Windows

Once this is set up, Tauon and the mirror start by themselves when you log in, in a minimised window.

1. Open `C:\Tauon\extra\windows\mirror` in File Explorer.
2. Click the address bar at the top, type `cmd`, and press **Enter**. A black window opens in that folder.
3. Type this and press **Enter**:

   ```
   tauon-mirror.cmd -Device Finamp -InstallStartup
   ```

Add other options to the same line, for example `-Tolerance 0.5`. To undo it, run `tauon-mirror.cmd -RemoveStartup` the same way. All options are listed in [mirror/README.md](mirror/README.md).

## What else syncs

The mirror only copies what's playing. Hearts, playlists and new music go through Jellyfin, and Tauon picks up changes from the phone only when you click **Import** again.

| What | Tauon → phone | Phone → Tauon |
| --- | --- | --- |
| Hearts (favourites) | Instant | On the next **Import music** in Tauon |
| New music in the folder | Jellyfin finds it and Finamp shows it | On the next **Import music** in Tauon |
| New playlists | Right-click the playlist tab in Tauon and choose **Upload** | **Import playlists** in Tauon |
| Changes to a playlist in both | **Upload** again; it replaces Jellyfin's copy | Not picked up: delete the tab in Tauon, then **Import playlists** |

- **Edit each playlist in one place only.** **Upload** from Tauon overwrites the playlist on Jellyfin, so songs you added in Finamp are lost.
- **Avoid double counting.** While mirroring, both Tauon and the phone report the song to Jellyfin, so play counts can double. In Tauon's Jellyfin settings, turn off **Allow local scrobbling**. If Finamp also scrobbles to Last.fm or ListenBrainz, turn off that scrobbler in Tauon too.
- **New music routine:** after adding albums, open **Settings > Connect > Jellyfin** in Tauon and click **Import music**. The mirror can only show songs that are in Tauon's library.

## Troubleshooting

Most problems come down to a wrong address, the firewall, or a song missing from Tauon's library.

| What you see | What to do |
| --- | --- |
| The phone can't reach `http://192.168.x.x:8096` | Set the PC's network to **Private** and allow Jellyfin through the firewall (Step 3). Run `ipconfig` again in case the address changed. |
| Tauon's **Test connectivity** fails | Check that the Server URL has `http://` and `:8096`, and that Jellyfin is running (tray icon, or **Services** app if installed as a service). |
| Tauon's login fails with error 500 | That's a Jellyfin server bug, not a Tauon problem. Restart the Jellyfin server, or update it to the latest version. |
| Jellyfin finds no music (service install) | The service can't read some places, like network drives or another user's folder. Move the music to a plain folder such as `D:\Music`, or reinstall with the basic (tray) option. |
| The window says **You need MSYS2** | Install MSYS2 to `C:\msys64` (see What you need), then double-click **tauon-mirror.cmd** again. |
| The window says **No Jellyfin login** | Sign in to Jellyfin in Tauon (Step 4), then start **tauon-mirror.cmd** again. |
| The window says **Tauon is open, but its remote control is off** | Close Tauon completely, also from the tray (right-click its icon, **Exit**). The script waits, switches remote control on and opens Tauon again. |
| Tauon pauses instead of playing the phone's song | That song isn't in Tauon's library. Click **Import music** in Tauon's Jellyfin settings. |
| Tauon is a few seconds behind the phone | Lower Finamp's **Playback Session Update Frequency** (Step 6a), or start the mirror with `-Tolerance 0.5` (Step 7 shows how to type options). |
| Tauon stays silent after the mirror stopped | Turn Tauon's volume back up. If the window was closed mid-song, the mirror also restores the volume the next time it starts. |
| The phone died mid-song and Tauon keeps playing | Normal: Tauon pauses about 20 seconds after that song should have ended. |
| Building Tauon fails | Run `tauon-mirror.cmd` again. If it keeps failing, open **MSYS2 CLANG64** from the Start menu, run `pacman -Syu`, then run `tauon-mirror.cmd -Rebuild`. The log is in `%LOCALAPPDATA%\tauon-mirror`. |
