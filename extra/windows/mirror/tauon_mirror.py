#!/usr/bin/env python3
"""tauon-mirror: make Tauon Music Box follow what a Jellyfin client is playing.

Your phone plays the music (into the speaker). Tauon on the PC follows along -
same track, same position, same pause state - and serves as the big
now-playing screen.

While it follows, Tauon's volume is set to 0. Tauon's visualizer reads the
audio before the volume stage, so it keeps moving. When the phone pauses or
stops, Tauon pauses too, its volume is restored and it is yours again until
the phone plays something.

Requirements:
  * Tauon: Settings > Connect > Sharing > "Enable remote control", then restart Tauon.
	On Windows, tauon-mirror.ps1 next to this file turns it on and starts Tauon.
  * The phone's music must be in Tauon's library, normally through Tauon's
	Jellyfin import. Tracks are matched by file path, with artist + title +
	length as the fallback.
  * The phone app must report playback to Jellyfin (Finamp, Jellyfin, ...).

Standard library only. Runs on Linux, Windows and macOS (Python 3.9+).
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import logging.handlers
import math
import os
import re
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

VERSION = "0.2.0"
WINDOWS = sys.platform == "win32"


def state_directory() -> Path:
	if WINDOWS:
		return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "tauon-mirror"
	return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "tauon-mirror"


STATE_FILE = state_directory() / "state.json"

TICKS_PER_MS = 10_000
STOP_GRACE = 3.0  # s without a phone session before Tauon is paused and handed back
# Tauon's player has a single command slot: a command sent before the player
# picked up the previous one replaces it. So commands are spaced out.
COMMAND_GAP = 1.0
STOP_GAP = 1.5  # a stop takes Tauon's player ~0.6 s
START_TIMEOUT = 5.0  # s to wait for Tauon to switch track before trying again
MAX_START_TRIES = 3
START_SETTLE = 1.5  # s after starting a track before its position is corrected
SEEK_COOLDOWN = 5.0  # s between corrective seeks while playing, so a buffering stream can't cause a seek loop
SEEK_LEAD_MS = 300  # Tauon needs a moment to resume a network stream after seeking
END_MARGIN_MS = 3000  # Tauon skips to the next track when asked to seek to its last 0.5 s
STALE_AFTER_END_MS = 20_000  # "playing" this long past the track's end: the phone is gone (Wi-Fi, battery)
INDEX_RETRY = 30.0  # s between index rebuilds caused by a track that wasn't found
CONFIDENT_AFTER = 1.6  # s, see PositionClock
MAX_BACKOFF = 5.0  # s between retries while Tauon or Jellyfin can't be reached

log = logging.getLogger("tauon-mirror")

# Both servers are on the LAN or this machine: never send them through an http_proxy
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class RequestError(Exception):
	def __init__(self, status: int | None, message: str) -> None:
		super().__init__(message)
		self.status = status


def request(method: str, url: str, headers: dict[str, str] | None = None, body: object = None, timeout: float = 5.0):
	data = None if body is None else json.dumps(body).encode()
	req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
	if data is not None:
		req.add_header("Content-Type", "application/json")
	try:
		with _opener.open(req, timeout=timeout) as resp:
			raw = resp.read()
	except urllib.error.HTTPError as e:
		raise RequestError(e.code, f"{method} {url}: HTTP {e.code}") from None
	except (OSError, http.client.HTTPException) as e:
		raise RequestError(None, f"{method} {url}: {getattr(e, 'reason', e)}") from None
	if not raw:
		return None
	try:
		return json.loads(raw)
	except ValueError:
		return raw.decode(errors="replace")


def parse_utc(value: str | None) -> datetime | None:
	"""Parse a Jellyfin timestamp like '2026-10-10T11:25:13.6663755Z'. None if missing or unset."""
	if not value or value.startswith("0001-"):
		return None
	m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?", value)
	if not m:
		return None
	fraction = (m.group(2) or "0")[:6].ljust(6, "0")
	return datetime.fromisoformat(f"{m.group(1)}.{fraction}+00:00")


def fmt_ms(ms: int) -> str:
	s = ms // 1000
	return f"{s // 60}:{s % 60:02d}"


# --- Jellyfin -----------------------------------------------------------------


class Jellyfin:
	def __init__(self, url: str, username: str | None = None, password: str | None = None, api_key: str | None = None) -> None:
		self.url = url.rstrip("/")
		self.username = username
		self.password = password
		self.token = api_key
		self.static_token = api_key is not None
		host = socket.gethostname().split(".")[0] or "pc"
		self.device_id = f"tauon-mirror-{host}"
		self._auth = f'MediaBrowser Client="Tauon Mirror", Device="{host}", DeviceId="{self.device_id}", Version="{VERSION}"'

	def _headers(self) -> dict[str, str]:
		return {"Authorization": self._auth + (f', Token="{self.token}"' if self.token else "")}

	def login(self) -> None:
		if self.static_token:
			return
		result = request(
			"POST",
			f"{self.url}/Users/AuthenticateByName",
			self._headers(),
			{"Username": self.username, "Pw": self.password},
			timeout=10,
		)
		self.token = result["AccessToken"]
		log.info("Logged in to Jellyfin at %s as %s", self.url, self.username)

	def get(self, path: str):
		if not self.token:
			self.login()
		try:
			return request("GET", self.url + path, self._headers())
		except RequestError as e:
			if e.status != 401 or self.static_token:
				raise
			self.token = None
			self.login()
			return request("GET", self.url + path, self._headers())

	def sessions(self) -> list[dict]:
		return self.get("/Sessions?activeWithinSeconds=900") or []

	def logout(self) -> None:
		if self.token and not self.static_token:
			try:
				request("POST", f"{self.url}/Sessions/Logout", self._headers(), {}, timeout=3)
			except RequestError:
				pass
			self.token = None


def is_own_or_tauon(session: dict, own_device_id: str) -> bool:
	# Tauon reports its own playback of Jellyfin tracks; following that would follow ourselves.
	return session.get("DeviceId") == own_device_id or (session.get("Client") or "").casefold().startswith("tauon")


def pick_session(sessions: list[dict], device: str | None, current_id: str | None, own_device_id: str) -> dict | None:
	"""The session to follow: playing over paused, then the one we already follow, then the most recent."""
	candidates = []
	for s in sessions:
		item = s.get("NowPlayingItem")
		if not item or item.get("MediaType", "Audio") != "Audio" or is_own_or_tauon(s, own_device_id):
			continue
		if device:
			wanted = device.casefold()
			names = ((s.get("DeviceName") or "").casefold(), (s.get("Client") or "").casefold())
			if not any(wanted in n for n in names) and wanted != (s.get("DeviceId") or "").casefold():
				continue
		candidates.append(s)
	if not candidates:
		return None
	return max(
		candidates,
		key=lambda s: (
			not (s.get("PlayState") or {}).get("IsPaused"),
			s.get("Id") == current_id,
			s.get("LastPlaybackCheckIn") or "",
			s.get("LastActivityDate") or "",
		),
	)


@dataclass(frozen=True)
class Target:
	"""What the followed session is playing, with the position extrapolated to now."""

	session_id: str
	device: str
	item_id: str
	path: str
	title: str
	artists: tuple[str, ...]
	duration_ms: int
	paused: bool
	position_ms: int
	confident: bool  # position is trustworthy enough to seek to
	stale: bool  # supposedly still playing, long after the track ended

	@property
	def label(self) -> str:
		return f"{', '.join(self.artists) or '?'} – {self.title}"


class PositionClock:
	"""Turns Jellyfin's occasional position reports into a running position.

	Clients report on events (track change, pause, seek) and otherwise only
	periodically (Finamp: every 150 s by default). Between reports the position
	is extrapolated. A report we see arrive is anchored at the moment we saw
	it. For a report that was already there when we started watching, the
	server's LastPlaybackCheckIn says how old it is. Some clients update the
	position more often than that timestamp, so such a position only counts as
	confident once a newer report arrived or CONFIDENT_AFTER has passed.
	"""

	def __init__(self) -> None:
		self.report: tuple | None = None
		self.anchor_ms = 0
		self.anchor_t = 0.0
		self.first_seen = 0.0
		self.fresh = False

	def reset(self) -> None:
		self.report = None

	def target(self, session: dict, now: float, utc_now: datetime) -> Target:
		item = session["NowPlayingItem"]
		state = session.get("PlayState") or {}
		ticks = state.get("PositionTicks") or 0
		paused = bool(state.get("IsPaused"))
		# The check-in time tells apart two reports of the same position, e.g. a track on repeat
		report = (session["Id"], item["Id"], ticks, paused, session.get("LastPlaybackCheckIn"))
		if report != self.report:
			same_item = self.report is not None and self.report[:2] == report[:2]
			self.anchor_ms = ticks // TICKS_PER_MS
			self.anchor_t = now
			if same_item:
				self.fresh = True
			else:
				self.first_seen = now
				self.fresh = False
				checkin = parse_utc(session.get("LastPlaybackCheckIn"))
				if checkin and not paused:
					age = (utc_now - checkin).total_seconds()
					if 0 < age < 3600:
						self.anchor_t = now - age
			self.report = report

		duration_ms = (item.get("RunTimeTicks") or 0) // TICKS_PER_MS
		position = self.anchor_ms if paused else self.anchor_ms + int((now - self.anchor_t) * 1000)
		stale = not paused and duration_ms > 0 and position > duration_ms + STALE_AFTER_END_MS
		if duration_ms:
			position = min(position, duration_ms)
		artists = item.get("Artists") or ([item["AlbumArtist"]] if item.get("AlbumArtist") else [])
		return Target(
			session_id=session["Id"],
			device=session.get("DeviceName") or session.get("Client") or "?",
			item_id=item["Id"],
			path=item.get("Path") or "",
			title=item.get("Name") or "",
			artists=tuple(artists),
			duration_ms=duration_ms,
			paused=paused,
			position_ms=max(0, position),
			confident=paused or self.fresh or now - self.first_seen >= CONFIDENT_AFTER,
			stale=stale,
		)


# --- Tauon --------------------------------------------------------------------


class Tauon:
	"""Tauon's remote control API (the one its phone apps use)."""

	def __init__(self, url: str) -> None:
		self.url = url.rstrip("/")

	def _get(self, path: str, timeout: float = 3.0):
		return request("GET", f"{self.url}/api1/{path}", timeout=timeout)

	def status(self) -> dict:
		return self._get("status")

	def playlists(self) -> list[dict]:
		return self._get("playlists")["playlists"]

	def tracklist(self, playlist_id: str) -> list[dict]:
		return self._get(f"tracklist/{playlist_id}", timeout=30)["tracks"]

	# Player commands. Send at most one per COMMAND_GAP, see Mirror._command.

	def start(self, playlist_id: str, position: int) -> None:
		self._get(f"start/{playlist_id}/{position}")

	def seek(self, ms: int) -> None:
		self._get(f"seek/{max(0, int(ms))}")

	def pause(self) -> None:
		self._get("pause")

	def play(self) -> None:
		self._get("play")

	def stop(self) -> None:
		self._get("stop")

	def set_volume(self, percent: int) -> None:
		self._get(f"setvolume/{max(0, min(100, int(percent)))}")


@dataclass(frozen=True)
class Slot:
	"""Where a track sits in Tauon. The API starts tracks by playlist + position."""

	playlist_id: str
	position: int
	track_id: int
	duration_ms: int


def norm_path(path: str) -> str:
	return path.replace("\\", "/").casefold()


def norm_text(text: str) -> str:
	return " ".join(text.casefold().split())


class TrackIndex:
	PREFERRED = "Jellyfin Collection"  # the playlist Tauon's Jellyfin import fills

	def __init__(self, tauon: Tauon) -> None:
		self.tauon = tauon
		self.by_path: dict[str, list[Slot]] = {}
		self.by_meta: dict[tuple[str, str], list[Slot]] = {}
		self.revision: object = None
		self.built = False

	def invalidate(self) -> None:
		self.built = False

	def ensure(self, revision: object) -> None:
		"""Build the index, or rebuild it when Tauon's library or playlists changed."""
		if not self.built or revision != self.revision:
			self.build(revision)

	def build(self, revision: object) -> None:
		by_path: dict[str, list[Slot]] = {}
		by_meta: dict[tuple[str, str], list[Slot]] = {}
		playlists = sorted(self.tauon.playlists(), key=lambda p: p["name"] != self.PREFERRED)
		count = 0
		for pl in playlists:
			for t in self.tauon.tracklist(pl["id"]):
				slot = Slot(pl["id"], t["position"], t["id"], t.get("duration") or 0)
				if t.get("path"):
					by_path.setdefault(norm_path(t["path"]), []).append(slot)
				# Tauon joins multiple Jellyfin artists with "; "
				artist = t.get("artist") or ""
				title = norm_text(t.get("title") or "")
				for name in {artist, *artist.split("; ")}:
					by_meta.setdefault((title, norm_text(name)), []).append(slot)
				count += 1
		self.by_path, self.by_meta = by_path, by_meta
		self.revision, self.built = revision, True
		log.info("Indexed %d tracks in %d Tauon playlists", count, len(playlists))

	def find(self, target: Target, prefer_playlist: str | None = None) -> Slot | None:
		slots = list(self.by_path.get(norm_path(target.path), [])) if target.path else []
		if not slots:
			title = norm_text(target.title)
			for artist in target.artists:
				for s in self.by_meta.get((title, norm_text(artist)), []):
					close = not s.duration_ms or not target.duration_ms or abs(s.duration_ms - target.duration_ms) <= 3000
					if close and s not in slots:
						slots.append(s)
		if not slots:
			return None
		return next((s for s in slots if s.playlist_id == prefer_playlist), slots[0])


class VolumeGuard:
	"""Remembers the volume to give back after mirroring.

	It is kept on disk, so a crash or reboot mid-mirror doesn't leave Tauon at 0.
	"""

	def __init__(self, enabled: bool, path: Path = STATE_FILE) -> None:
		self.enabled = enabled
		self.path = path
		self.saved = self._load()

	def _load(self) -> int | None:
		try:
			return int(json.loads(self.path.read_text())["volume"])
		except (OSError, ValueError, KeyError, TypeError):
			return None

	def save(self, volume: int) -> None:
		"""Remember the user's volume, unless it is already remembered."""
		if self.saved is not None:
			return
		self.path.parent.mkdir(parents=True, exist_ok=True)
		tmp = self.path.with_suffix(".tmp")
		tmp.write_text(json.dumps({"volume": volume}))
		tmp.replace(self.path)
		self.saved = volume

	def forget(self) -> None:
		self.saved = None
		self.path.unlink(missing_ok=True)


# --- The mirror ---------------------------------------------------------------


class Mirror:
	"""Drives Tauon to match the followed session, one player command at a time.

	Only a playing phone takes Tauon over. When the phone pauses, Tauon pauses
	on the same spot; when the phone stops, Tauon pauses. Either way Tauon then
	gets its volume back and is left alone until the phone plays again.
	"""

	def __init__(self, jellyfin: Jellyfin, tauon: Tauon, *, device: str | None, tolerance_ms: int, mute: bool,
				 state_file: Path = STATE_FILE) -> None:
		self.jf = jellyfin
		self.tauon = tauon
		self.device = device
		self.tolerance_ms = tolerance_ms
		self.clock = PositionClock()
		self.index = TrackIndex(tauon)
		self.volume = VolumeGuard(mute, state_file)
		self.recovering = self.volume.saved is not None  # a saved volume means we were interrupted mid-mirror
		self.session_id: str | None = None
		self.engaged = False
		self.stopped_since: float | None = None
		self.busy_until = -math.inf
		self.unramped = False
		self.pending: tuple[str, float] | None = None  # (item id, when) of the last start command
		self.start_tries: tuple[str, int] = ("", 0)
		self.settle_until = -math.inf
		self.last_seek = -math.inf
		self.last_index_miss = -math.inf
		self.warned: set[str] = set()

	def _warn_once(self, key: str, msg: str, *args: object) -> None:
		if key not in self.warned:
			self.warned.add(key)
			log.warning(msg, *args)

	def _command(self, now: float, action, *args: object, gap: float | None = None) -> None:
		action(*args)
		self.busy_until = now + (COMMAND_GAP if gap is None else gap)

	def tick(self, now: float | None = None, utc_now: datetime | None = None) -> None:
		now = time.monotonic() if now is None else now
		utc_now = datetime.now(timezone.utc) if utc_now is None else utc_now

		session = pick_session(self.jf.sessions(), self.device, self.session_id, self.jf.device_id)
		if session is None:
			self.clock.reset()
			self._on_stopped(now)
			return
		target = self.clock.target(session, now, utc_now)
		if target.session_id != self.session_id:
			log.info("Following %s (%s)", target.device, session.get("Client"))
			self.session_id = target.session_id
		if target.stale:
			self._warn_once(f"stale:{target.item_id}", "%s stopped reporting, treating it as stopped", target.device)
			self._on_stopped(now)
			return
		self.stopped_since = None
		if target.paused and not self.engaged:
			return
		if now < self.busy_until:
			return

		status = self.tauon.status()
		if not self.engaged:
			log.info("%s is playing – Tauon follows", target.device)
			self.engaged = True
			self.recovering = False
		if self._silence(status, now):
			return
		in_sync = self._sync(target, status, now)
		if target.paused and in_sync:
			log.info("⏸ Paused on %s – Tauon handed back", target.device)
			self._hand_back(now)

	def _on_stopped(self, now: float) -> None:
		if self.stopped_since is None:
			self.stopped_since = now
		if now < self.busy_until:
			return
		if self.recovering:
			self._recover(now)
			return
		if not self.engaged or now - self.stopped_since < STOP_GRACE:
			return
		if self.tauon.status().get("status") == "playing":
			log.info("⏹ Phone stopped – pausing Tauon")
			self._command(now, self.tauon.pause)
			return  # hand back once that went through
		self._hand_back(now)

	def _recover(self, now: float) -> None:
		"""Undo the mute of a run that was killed mid-mirror, unless the volume was changed since."""
		self.recovering = False
		if self.tauon.status().get("volume") == 0:
			log.info("Restoring Tauon's volume from an interrupted run")
			self._command(now, self.tauon.set_volume, self.volume.saved)
		self.volume.forget()

	def _hand_back(self, now: float) -> None:
		if self.volume.saved is not None:
			self._command(now, self.tauon.set_volume, self.volume.saved)
			self.volume.forget()
		self.engaged = False
		self.pending = None

	def _silence(self, status: dict, now: float) -> bool:
		"""Mute Tauon before it plays anything. True if that took a command."""
		if not self.volume.enabled:
			return False
		volume = round(status.get("volume") or 0)
		self.volume.save(volume)
		if volume != 0:
			self._command(now, self.tauon.set_volume, 0)
			self.unramped = status.get("status") == "stopped"
			return True
		if self.unramped:
			self.unramped = False
			if status.get("status") == "stopped":
				# Tauon fades volume changes in while audio plays, so the next track would
				# start with a fade from the old volume. A stop applies the volume at once.
				self._command(now, self.tauon.stop, gap=STOP_GAP)
				return True
		return False

	def _slot_for(self, target: Target, status: dict, now: float) -> Slot | None:
		self.index.ensure(status.get("inc"))
		slot = self.index.find(target, prefer_playlist=status.get("playlist"))
		if slot is None and now - self.last_index_miss >= INDEX_RETRY:
			# Maybe it was imported since we built the index
			self.last_index_miss = now
			self.index.build(status.get("inc"))
			slot = self.index.find(target, prefer_playlist=status.get("playlist"))
		if slot is None:
			self._warn_once(f"missing:{target.item_id}", "Not in Tauon's library: %s (%s)", target.label, target.path)
		return slot

	def _sync(self, target: Target, status: dict, now: float) -> bool:
		"""Send Tauon the next command towards the target. True once it's there."""
		state = status.get("status")
		slot = self._slot_for(target, status, now)
		if slot is None:
			if state == "playing":
				self._command(now, self.tauon.pause)  # better than showing the wrong track
				return False
			return True

		if status.get("id") != slot.track_id or state == "stopped":
			return self._start(target, slot, now)
		self.start_tries = ("", 0)
		if self.pending:
			# Tauon reports the new track at once, but its player may still be opening
			# it, and a command now would replace the open.
			if not status.get("progress") and now - self.pending[1] < START_TIMEOUT:
				return False
			self.pending = None

		if target.paused and state == "playing":
			self._command(now, self.tauon.pause)
			return False
		if not target.paused and state == "paused":
			self._command(now, self.tauon.play)
			return False
		if not target.confident:
			return False

		drift = (status.get("progress") or 0) - target.position_ms
		if abs(drift) <= self.tolerance_ms:
			return True
		if not target.paused and (now < self.settle_until or now - self.last_seek < SEEK_COOLDOWN):
			return False
		if target.duration_ms and target.position_ms > target.duration_ms - END_MARGIN_MS:
			return True
		log.info("↔ Tauon %+.1fs off, seeking to %s", drift / 1000, fmt_ms(target.position_ms))
		self._command(now, self.tauon.seek, target.position_ms + (0 if target.paused else SEEK_LEAD_MS))
		self.last_seek = now
		return False

	def _start(self, target: Target, slot: Slot, now: float) -> bool:
		if self.pending and self.pending[0] == target.item_id:
			if now - self.pending[1] < START_TIMEOUT:
				return False  # Tauon is still switching
			self.index.invalidate()  # the playlists probably changed under us; rebuild before the retry
			self.pending = None
			return False
		item, tries = self.start_tries
		tries = tries + 1 if item == target.item_id else 1
		self.start_tries = (target.item_id, tries)
		if tries > MAX_START_TRIES:
			self._warn_once(f"start:{target.item_id}", "Tauon won't switch to %s, skipping it", target.label)
			return True
		log.info("▶ %s%s", target.label, " (paused)" if target.paused else "")
		self._command(now, self.tauon.start, slot.playlist_id, slot.position)
		self.pending = (target.item_id, now)
		self.settle_until = now + START_SETTLE
		return False

	def shutdown(self) -> None:
		"""Leave Tauon paused at its own volume."""
		try:
			if self.engaged and self.tauon.status().get("status") == "playing":
				self.tauon.pause()
				time.sleep(COMMAND_GAP)
			if self.volume.saved is not None and (self.engaged or self.recovering):
				self.tauon.set_volume(self.volume.saved)
				self.volume.forget()
		except RequestError as e:
			log.warning("Couldn't hand Tauon back: %s", e)
		self.jf.logout()


# --- CLI ----------------------------------------------------------------------


def source_checkout_root(start: Path) -> Path | None:
	"""The Tauon checkout this file sits in, found the way Tauon's launcher finds its own."""
	for directory in [start, *start.parents]:
		if (directory / "pyproject.toml").is_file() and (directory / "src/tauon/__main__.py").is_file():
			return directory
	return None


def tauon_conf_candidates(env: dict[str, str], platform: str, home: Path, here: Path) -> list[Path]:
	"""Where Tauon may keep tauon.conf, mirroring user_data_directory() in src/tauon_native/main.cpp.

	A portable Tauon (a "portable" file beside its exe) keeps it in user-data
	next to the exe, which can't be guessed: pass --tauon-conf for that.
	"""
	if env.get("TAUON_USER_DATA_DIR"):
		return [Path(env["TAUON_USER_DATA_DIR"]) / "tauon.conf"]
	dirs: list[Path] = []
	if platform == "win32":
		# Installed and source builds alike; Windows has no source-checkout location
		for var in ("LOCALAPPDATA", "APPDATA"):
			if env.get(var):
				dirs.append(Path(env[var]) / "TauonMusicBox")
		dirs.append(home / "AppData/Local/TauonMusicBox")
	else:
		dirs.append(home / ".var/app/com.github.taiko2k.tauonmb/data/TauonMusicBox")  # Flatpak
		if platform.startswith("linux"):
			root = source_checkout_root(here)
			if root:
				dirs.append(root / "user-data")  # launcher run from this checkout's venv
		if env.get("XDG_DATA_HOME"):
			dirs.append(Path(env["XDG_DATA_HOME"]) / "TauonMusicBox")
		dirs.append(home / ".local/share/TauonMusicBox")
	unique: list[Path] = []
	for d in dirs:
		if d / "tauon.conf" not in unique:
			unique.append(d / "tauon.conf")
	return unique


def find_tauon_conf(explicit: str | None = None) -> Path | None:
	"""The tauon.conf to read: the given one, else the most recently saved candidate."""
	explicit = explicit or os.environ.get("TAUON_CONF")
	if explicit:
		return Path(explicit).expanduser()
	here = Path(__file__).resolve().parent
	existing = [p for p in tauon_conf_candidates(dict(os.environ), sys.platform, Path.home(), here) if p.is_file()]
	if not existing:
		return None
	return max(existing, key=lambda p: p.stat().st_mtime)


def read_conf_values(text: str, keys: list[str]) -> dict[str, str]:
	"""Read keys from tauon.conf the way Tauon's Config.sync_add does.

	It isn't strict TOML: strings are written without escaping, and on Windows
	lines end in CR CR LF, which tomllib rejects. The first `key = value` line
	wins, whatever its section.
	"""
	values: dict[str, str] = {}
	for line in text.splitlines():
		row = line.split(" #", 1)[0]
		if "=" not in row:
			continue
		key, value = (part.strip() for part in row.split("=", 1))
		if key in keys and key not in values and value:
			if len(value) >= 2 and value[0] == value[-1] == '"':
				value = value[1:-1]
			values[key] = value
	return values


def read_tauon_jellyfin_account(conf: Path | None) -> dict[str, str]:
	if conf is None:
		log.debug("No tauon.conf found")
		return {}
	try:
		text = conf.read_text(encoding="utf-8", errors="replace")
	except OSError as e:
		log.debug("Couldn't read %s: %s", conf, e)
		return {}
	values = read_conf_values(text, ["jelly-server-url", "jelly-username", "jelly-password"])
	return {
		"url": values.get("jelly-server-url", ""),
		"username": values.get("jelly-username", ""),
		"password": values.get("jelly-password", ""),
	}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
	p = argparse.ArgumentParser(
		description="Make Tauon Music Box follow what your phone plays through Jellyfin.",
		epilog="Jellyfin login: JELLYFIN_API_KEY, else JELLYFIN_USERNAME/JELLYFIN_PASSWORD, "
		"else the Jellyfin account configured in Tauon.",
	)
	p.add_argument("--device", help="follow only this device (part of its name in Jellyfin, e.g. 'moto' or 'Finamp')")
	p.add_argument("--jellyfin-url", help="default: the server configured in Tauon")
	p.add_argument("--tauon-url", default="http://127.0.0.1:7814", help="Tauon remote API (default: %(default)s)")
	p.add_argument("--interval", type=float, default=0.5, help="seconds between checks (default: %(default)s)")
	p.add_argument("--tolerance", type=float, default=2.5, help="seconds Tauon may drift before it is re-synced (default: %(default)s)")
	p.add_argument("--no-mute", action="store_true", help="leave Tauon's volume alone")
	p.add_argument("--tauon-conf", help="Tauon's tauon.conf, for its Jellyfin login (default: $TAUON_CONF, else found automatically)")
	p.add_argument("--log-file", help="also log to this file (default without a console: tauon-mirror.log beside the state file)")
	p.add_argument("-v", "--verbose", action="store_true")
	p.add_argument("--version", action="version", version=VERSION)
	return p.parse_args(argv)


def setup_logging(verbose: bool, log_file: str | None) -> None:
	handlers: list[logging.Handler] = []
	stream = sys.stderr
	if stream is not None:  # None under pythonw
		if hasattr(stream, "reconfigure"):
			# A redirected stream on Windows uses the ANSI code page, which lacks ▶ and friends
			stream.reconfigure(errors="backslashreplace")
		console = logging.StreamHandler(stream)
		fmt = "%(asctime)s %(levelname)-7s %(message)s" if stream.isatty() else "%(levelname)-7s %(message)s"
		console.setFormatter(logging.Formatter(fmt, "%H:%M:%S"))
		handlers.append(console)
	elif not log_file:
		log_file = str(state_directory() / "tauon-mirror.log")
	if log_file:
		path = Path(log_file).expanduser()
		try:
			path.parent.mkdir(parents=True, exist_ok=True)
			to_file = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
		except OSError as e:
			if stream is not None:
				print(f"Can't log to {path}: {e}", file=stream)
		else:
			to_file.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
			handlers.append(to_file)
	if not handlers:
		handlers.append(logging.NullHandler())
	logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers, force=True)


def handle_stop_signals(stop, finished: threading.Event) -> object:
	"""Call stop() on Ctrl+C, Ctrl+Break, SIGTERM, and on Windows when the console closes.

	Returns what must stay referenced for the handlers to keep working.
	"""
	signal.signal(signal.SIGINT, stop)
	signal.signal(signal.SIGTERM, stop)  # never sent on Windows, harmless there
	if not WINDOWS:
		return None
	signal.signal(signal.SIGBREAK, stop)  # type: ignore[attr-defined]
	import ctypes
	from ctypes import wintypes

	close_events = {2, 5, 6}  # CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT

	@ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
	def on_console_event(event: int) -> bool:
		if event not in close_events:
			return False  # Ctrl+C and Ctrl+Break go on to the signal handlers
		# Windows ends the process once this returns, so hold it until Tauon is handed back
		stop()
		finished.wait(4.5)
		return True

	try:
		ctypes.windll.kernel32.SetConsoleCtrlHandler(on_console_event, True)  # type: ignore[attr-defined]
	except (AttributeError, OSError):
		return None
	return on_console_event


def main(argv: list[str] | None = None) -> int:
	args = parse_args(argv)
	setup_logging(args.verbose, args.log_file)

	conf = find_tauon_conf(args.tauon_conf)
	log.debug("Tauon config: %s", conf)
	account = read_tauon_jellyfin_account(conf)
	url = args.jellyfin_url or os.environ.get("JELLYFIN_URL") or account.get("url")
	api_key = os.environ.get("JELLYFIN_API_KEY")
	username = os.environ.get("JELLYFIN_USERNAME") or account.get("username")
	password = os.environ.get("JELLYFIN_PASSWORD") or account.get("password")
	if not url or not (api_key or username):
		log.error("No Jellyfin server/login found (Tauon config: %s). Set up Jellyfin in Tauon, or see --help.",
		          conf or "not found")
		return 2

	jellyfin = Jellyfin(url, username, password, api_key)
	tauon = Tauon(args.tauon_url)
	mirror = Mirror(jellyfin, tauon, device=args.device, tolerance_ms=int(args.tolerance * 1000), mute=not args.no_mute)

	running = True
	finished = threading.Event()

	def stop(*args: object) -> None:
		nonlocal running
		running = False

	keep_alive = handle_stop_signals(stop, finished)

	log.info("tauon-mirror %s: Jellyfin %s → Tauon %s%s", VERSION, jellyfin.url, tauon.url,
			 f" (device: {args.device})" if args.device else "")
	errors = 0
	while running:
		try:
			mirror.tick()
			if errors:
				log.info("Connected again")
			errors = 0
		except RequestError as e:
			errors += 1
			# Tauon closed, remote control off, Wi-Fi gone... say it once, then keep trying quietly
			(log.warning if errors == 1 else log.debug)("%s", e)
		except Exception:
			errors += 1
			log.exception("Unexpected error")
		delay = args.interval if errors == 0 else min(MAX_BACKOFF, args.interval * 2 ** min(errors, 8))
		until = time.monotonic() + delay
		while running and time.monotonic() < until:
			time.sleep(min(0.1, delay))

	try:
		mirror.shutdown()
		log.info("Stopped")
	finally:
		finished.set()
	del keep_alive
	return 0


if __name__ == "__main__":
	sys.exit(main())
