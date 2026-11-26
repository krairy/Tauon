import json
import logging
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tauon_mirror as tm

UTC0 = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
MIN = 60_000


def track(tid, title, artist, path, duration_ms):
	return {"id": tid, "title": title, "artist": artist, "path": path, "duration": duration_ms}


LIBRARY = {
	"111": ("Jellyfin Collection", [
		track(1, "Song A", "Artist X", "/music/X/a.flac", 4 * MIN),
		track(2, "Song B", "Artist X; Artist Y", "/music/X/b.flac", 3 * MIN),
		track(3, "Song C", "Artist Z", "/music/Z/c.flac", 5 * MIN),
	]),
	"222": ("Favourites", [
		track(3, "Song C", "Artist Z", "/music/Z/c.flac", 5 * MIN),
	]),
}


class FakeTauon:
	"""Tauon's remote API. Player commands land in a single slot like in Tauon itself:
	sending one while another hasn't been picked up yet is a test failure."""

	def __init__(self, library=LIBRARY, volume=70):
		self.library = library
		self.state = "stopped"
		self.track_id = None
		self.playlist = "111"
		self.progress = 0
		self.volume = volume
		self.inc = 1
		self.commands = []
		self.slot_busy = False
		self.strict = True
		self.now = 0.0
		self.sent_at = []

	def consume(self):
		"""Tauon's player thread takes the queued command."""
		self.slot_busy = False

	def play_for(self, ms):
		if self.state == "playing":
			self.progress += ms

	def _command(self, *cmd):
		assert not (self.strict and self.slot_busy), f"{cmd} would replace the unconsumed {self.commands[-1]}"
		self.commands.append(cmd)
		self.sent_at.append(self.now)
		self.slot_busy = True

	def status(self):
		s = {"status": self.state, "inc": self.inc, "progress": self.progress, "volume": float(self.volume),
			 "playlist": self.playlist}
		if self.track_id is not None:
			s["id"] = self.track_id
		return s

	def playlists(self):
		return [{"id": pid, "name": name, "count": len(tracks)} for pid, (name, tracks) in self.library.items()]

	def tracklist(self, pid):
		return [dict(t, position=i) for i, t in enumerate(self.library[pid][1])]

	def start(self, pid, pos):
		self._command("start", pid, pos)
		self.playlist, self.track_id = pid, self.library[pid][1][pos]["id"]
		self.state, self.progress = "playing", 0

	def seek(self, ms):
		self._command("seek", ms)
		self.progress = ms

	def pause(self):
		self._command("pause")
		if self.state == "playing":
			self.state = "paused"

	def play(self):
		self._command("play")
		if self.state == "paused":
			self.state = "playing"

	def stop(self):
		self._command("stop")
		self.state, self.progress = "stopped", 0

	def set_volume(self, v):
		self._command("volume", v)
		self.volume = v


class FakeJellyfin:
	device_id = "tauon-mirror-test"

	def __init__(self):
		self.list = []
		self.commands = []
		self.fail = False

	def sessions(self):
		return self.list

	def command(self, session_id, name, arguments):
		if self.fail:
			raise tm.RequestError(403, "POST /Sessions/x/Command: HTTP 403")
		self.commands.append((session_id, name, arguments))

	def logout(self):
		pass


def session(item="a", path="/music/X/a.flac", title="Song A", artists=("Artist X",), duration_ms=4 * MIN,
			position_ms=0, paused=False, sid="s1", client="Finamp", device="OnePlus Nord", checkin=UTC0):
	return {
		"Id": sid, "Client": client, "DeviceName": device, "DeviceId": f"dev-{sid}",
		"LastPlaybackCheckIn": checkin.isoformat().replace("+00:00", "0Z") if checkin else None,
		"NowPlayingItem": {"Id": item, "Path": path, "Name": title, "Artists": list(artists),
						   "RunTimeTicks": duration_ms * tm.TICKS_PER_MS, "MediaType": "Audio"},
		"PlayState": {"PositionTicks": position_ms * tm.TICKS_PER_MS, "IsPaused": paused, "VolumeLevel": 100},
		"SupportedCommands": ["SetVolume"],
	}


class Harness:
	def __init__(self, test, volume=70, state=None):
		self.tmp = tempfile.TemporaryDirectory()
		test.addCleanup(self.tmp.cleanup)
		self.state_file = Path(self.tmp.name) / "state.json"
		if state is not None:
			self.state_file.write_text(json.dumps(state))
		self.tauon = FakeTauon(volume=volume)
		self.jf = FakeJellyfin()
		self.mirror = tm.Mirror(self.jf, self.tauon, device=None, tolerance_ms=2500, mute=True, state_file=self.state_file)
		self.t = 1000.0

	def step(self, seconds=0.5, consume=True):
		"""Let time pass (Tauon plays), then run one bridge tick."""
		self.t += seconds
		if consume:
			self.tauon.consume()
		self.tauon.play_for(int(seconds * 1000))
		self.tauon.now = self.t
		self.mirror.tick(self.t, UTC0 + timedelta(seconds=self.t - 1000.0))

	def run(self, seconds, step=0.5):
		for _ in range(int(seconds / step)):
			self.step(step)


class ParseUtcTest(unittest.TestCase):
	def test_seven_digit_fraction(self):
		self.assertEqual(tm.parse_utc("2026-10-10T11:25:13.6663755Z"),
						 datetime(2026, 10, 10, 11, 25, 13, 666375, tzinfo=timezone.utc))

	def test_unset(self):
		self.assertIsNone(tm.parse_utc("0001-01-01T00:00:00.0000000Z"))
		self.assertIsNone(tm.parse_utc(None))
		self.assertIsNone(tm.parse_utc(""))


class PickSessionTest(unittest.TestCase):
	def test_ignores_tauon_self_video_and_idle(self):
		video = session(sid="tv")
		video["NowPlayingItem"]["MediaType"] = "Video"
		idle = session(sid="idle")
		del idle["NowPlayingItem"]
		own = session(sid="own")
		own["DeviceId"] = "me"
		sessions = [session(sid="t", client="Tauon"), video, idle, own]
		self.assertIsNone(tm.pick_session(sessions, None, None, "me"))

	def test_playing_beats_paused_and_current_beats_newer(self):
		paused = session(sid="p", paused=True, checkin=UTC0 + timedelta(seconds=9))
		old = session(sid="old", checkin=UTC0)
		new = session(sid="new", checkin=UTC0 + timedelta(seconds=5))
		self.assertEqual(tm.pick_session([paused, old, new], None, None, "me")["Id"], "new")
		self.assertEqual(tm.pick_session([paused, old, new], None, "old", "me")["Id"], "old")

	def test_device_filter(self):
		phone = session(sid="phone", device="OnePlus Nord")
		web = session(sid="web", device="Firefox", client="Jellyfin Web")
		self.assertEqual(tm.pick_session([phone, web], "oneplus", None, "me")["Id"], "phone")
		self.assertEqual(tm.pick_session([phone, web], "jellyfin web", None, "me")["Id"], "web")
		self.assertIsNone(tm.pick_session([phone, web], "pixel", None, "me"))


class PositionClockTest(unittest.TestCase):
	def test_extrapolates_from_checkin_age(self):
		clock = tm.PositionClock()
		s = session(position_ms=10_000, checkin=UTC0)
		t = clock.target(s, 100.0, UTC0 + timedelta(seconds=4))
		self.assertEqual(t.position_ms, 14_000)
		self.assertFalse(t.confident)
		t = clock.target(s, 102.0, UTC0 + timedelta(seconds=6))
		self.assertEqual(t.position_ms, 16_000)
		self.assertTrue(t.confident)

	def test_new_report_is_fresh(self):
		clock = tm.PositionClock()
		clock.target(session(position_ms=10_000), 100.0, UTC0)
		t = clock.target(session(position_ms=60_000), 100.5, UTC0)
		self.assertTrue(t.confident)
		self.assertEqual(t.position_ms, 60_000)
		self.assertEqual(clock.target(session(position_ms=60_000), 103.5, UTC0).position_ms, 63_000)

	def test_paused_holds_and_clamps(self):
		clock = tm.PositionClock()
		t = clock.target(session(position_ms=30_000, paused=True), 100.0, UTC0 + timedelta(seconds=50))
		self.assertEqual(t.position_ms, 30_000)
		self.assertTrue(t.confident)
		clock.reset()
		t = clock.target(session(position_ms=4 * MIN - 1000), 100.0, UTC0)
		t = clock.target(session(position_ms=4 * MIN - 1000), 110.0, UTC0)
		self.assertEqual(t.position_ms, 4 * MIN)
		self.assertFalse(t.stale)
		t = clock.target(session(position_ms=4 * MIN - 1000), 130.0, UTC0)
		self.assertTrue(t.stale)


class TrackIndexTest(unittest.TestCase):
	def setUp(self):
		self.index = tm.TrackIndex(FakeTauon())
		self.index.ensure(1)

	def target(self, **kw):
		return tm.PositionClock().target(session(**kw), 0.0, UTC0)

	def test_path_match_ignores_case(self):
		slot = self.index.find(self.target(path="/MUSIC/X/a.flac"))
		self.assertEqual((slot.playlist_id, slot.position, slot.track_id), ("111", 0, 1))

	def test_metadata_fallback_with_split_artists(self):
		slot = self.index.find(self.target(path="/elsewhere/b.mp3", title="song  b", artists=("Artist Y",),
										   duration_ms=3 * MIN + 2000))
		self.assertEqual(slot.track_id, 2)
		self.assertIsNone(self.index.find(self.target(path="", title="Song B", artists=("Artist Y",),
													  duration_ms=3 * MIN + 10_000)))

	def test_prefers_playing_playlist(self):
		t = self.target(path="/music/Z/c.flac")
		self.assertEqual(self.index.find(t).playlist_id, "111")
		self.assertEqual(self.index.find(t, prefer_playlist="222").playlist_id, "222")


class TauonConfTest(unittest.TestCase):
	# As Tauon writes it on Windows: os.linesep through a text-mode file gives CR CR LF
	CONF = (
		'# Tauon Music Box configuration file\r\r\n'
		'[app]\r\r\n'
		'enable-remote-interface = true    # For use with Tauon Music Remote for Android\r\r\n'
		'[jellyfin_account]\r\r\n'
		'jelly-username = "me"\r\r\n'
		'jelly-password = "p\\a"ss"\r\r\n'
		'jelly-server-url = "http://jellyfin.local:8096"  # Remember to include the port\r\r\n'
		'jelly-username = "later duplicate"\r\r\n'
	)

	def test_reads_like_tauon(self):
		values = tm.read_conf_values(self.CONF, ["jelly-username", "jelly-password", "jelly-server-url", "missing"])
		self.assertEqual(values, {
			"jelly-username": "me",
			"jelly-password": 'p\\a"ss',
			"jelly-server-url": "http://jellyfin.local:8096",
		})

	def test_account_from_file(self):
		with tempfile.TemporaryDirectory() as tmp:
			conf = Path(tmp) / "tauon.conf"
			conf.write_bytes(self.CONF.encode())
			account = tm.read_tauon_jellyfin_account(conf)
		self.assertEqual(account["url"], "http://jellyfin.local:8096")
		self.assertEqual(account["username"], "me")
		self.assertEqual(tm.read_tauon_jellyfin_account(None), {})

	def test_windows_candidates(self):
		env = {"LOCALAPPDATA": r"C:\Users\u\AppData\Local", "APPDATA": r"C:\Users\u\AppData\Roaming"}
		found = tm.tauon_conf_candidates(env, "win32", Path("/home/u"), Path("/nowhere"))
		self.assertEqual(found[:2], [Path(env["LOCALAPPDATA"]) / "TauonMusicBox/tauon.conf",
		                             Path(env["APPDATA"]) / "TauonMusicBox/tauon.conf"])

	def test_user_data_override(self):
		env = {"TAUON_USER_DATA_DIR": "/data/tauon", "LOCALAPPDATA": "/x"}
		self.assertEqual(tm.tauon_conf_candidates(env, "win32", Path("/home/u"), Path("/nowhere")),
		                 [Path("/data/tauon/tauon.conf")])

	def test_linux_candidates_include_source_checkout(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			(root / "src/tauon").mkdir(parents=True)
			(root / "src/tauon/__main__.py").write_text("")
			(root / "pyproject.toml").write_text("")
			here = root / "extra/windows/mirror"
			here.mkdir(parents=True)
			found = tm.tauon_conf_candidates({"XDG_DATA_HOME": "/xdg"}, "linux", Path("/home/u"), here)
		self.assertEqual(found, [
			Path("/home/u/.var/app/com.github.taiko2k.tauonmb/data/TauonMusicBox/tauon.conf"),
			root / "user-data/tauon.conf",
			Path("/xdg/TauonMusicBox/tauon.conf"),
			Path("/home/u/.local/share/TauonMusicBox/tauon.conf"),
		])

	def test_explicit_conf_wins(self):
		self.assertEqual(tm.find_tauon_conf("/some/tauon.conf"), Path("/some/tauon.conf"))


class LoggingTest(unittest.TestCase):
	def test_no_console_logs_to_file(self):
		# pythonw.exe has no stderr
		root = logging.getLogger()
		saved_handlers, saved_level = root.handlers[:], root.level
		self.addCleanup(lambda: (setattr(root, "handlers", saved_handlers), root.setLevel(saved_level)))
		stderr, sys.stderr = sys.stderr, None
		self.addCleanup(setattr, sys, "stderr", stderr)
		with tempfile.TemporaryDirectory() as tmp:
			log_file = Path(tmp) / "sub/mirror.log"
			tm.setup_logging(False, str(log_file))
			tm.log.info("▶ hello")
			for h in root.handlers:
				h.close()
			self.assertIn("▶ hello", log_file.read_text(encoding="utf-8"))


class MirrorTest(unittest.TestCase):
	def setUp(self):
		logging.disable(logging.CRITICAL)
		self.addCleanup(logging.disable, logging.NOTSET)

	def test_paused_phone_is_ignored(self):
		h = Harness(self)
		h.jf.list = [session(paused=True, position_ms=5000)]
		h.run(5)
		self.assertEqual(h.tauon.commands, [])
		self.assertFalse(h.state_file.exists())

	def test_follows_from_stopped_tauon(self):
		h = Harness(self, volume=70)
		h.jf.list = [session(position_ms=30_000)]
		h.run(8)
		cmds = [c[0] for c in h.tauon.commands]
		# mute, apply the mute at once while stopped, start, then catch up with the phone
		self.assertEqual(cmds[:4], ["volume", "stop", "start", "seek"])
		self.assertEqual(json.loads(h.state_file.read_text()), {"volume": 70})
		self.assertEqual((h.tauon.state, h.tauon.track_id, h.tauon.volume), ("playing", 1, 0))
		phone_pos = 30_000 + (h.t - 1000.0) * 1000
		self.assertLess(abs(h.tauon.progress - phone_pos), 1000)

		h.tauon.commands.clear()
		h.run(10)
		self.assertEqual(h.tauon.commands, [])  # in sync: leave it alone

	def test_commands_are_spaced(self):
		# Tauon's player keeps one command at a time; a quick second one would replace the first
		h = Harness(self)
		b = dict(item="b", path="/music/X/b.flac", title="Song B", duration_ms=3 * MIN)
		for sessions, seconds in [
			([session(position_ms=30_000)], 6),
			([session(**b)], 0.5),
			([session(**b, position_ms=60_000, paused=True)], 0.5),
			([session(**b, position_ms=60_000)], 3),
			([session(item="c", path="/music/Z/c.flac", title="Song C", artists=("Artist Z",), duration_ms=5 * MIN)], 4),
			([], 8),
			([session(position_ms=90_000)], 6),
		]:
			h.jf.list = sessions
			h.run(seconds)
		self.assertGreater(len(h.tauon.sent_at), 10)
		gaps = [b - a for a, b in zip(h.tauon.sent_at, h.tauon.sent_at[1:])]
		self.assertGreaterEqual(min(gaps), 1.0)  # not tm.COMMAND_GAP: this is what Tauon needs
		self.assertEqual((h.tauon.track_id, h.tauon.state, h.tauon.volume), (1, "playing", 0))

	def test_phone_track_change_pause_and_resume(self):
		h = Harness(self)
		h.jf.list = [session()]
		h.run(6)
		h.jf.list = [session(item="b", path="/music/X/b.flac", title="Song B", duration_ms=3 * MIN,
							 checkin=UTC0 + timedelta(seconds=6))]
		h.run(3)
		self.assertEqual((h.tauon.track_id, h.tauon.state), (2, "playing"))

		h.jf.list = [session(item="b", path="/music/X/b.flac", title="Song B", duration_ms=3 * MIN,
							 position_ms=20_000, paused=True)]
		h.run(6)
		self.assertEqual(h.tauon.state, "paused")
		self.assertEqual(h.tauon.progress, 20_000)
		self.assertEqual(h.tauon.volume, 70)  # handed back
		self.assertFalse(h.mirror.engaged)
		self.assertFalse(h.state_file.exists())

		h.tauon.commands.clear()
		h.jf.list = [session(item="b", path="/music/X/b.flac", title="Song B", duration_ms=3 * MIN,
							 position_ms=20_000)]
		h.run(3)
		self.assertEqual([c[0] for c in h.tauon.commands], ["volume", "play"])
		self.assertEqual((h.tauon.state, h.tauon.volume), ("playing", 0))

	def test_pause_at_start_of_next_track_is_a_switch(self):
		h = Harness(self)
		h.jf.list = [session()]
		h.run(6)
		# Finamp, then nothing more for minutes
		h.jf.list = [session(item="b", path="/music/X/b.flac", title="Song B", duration_ms=3 * MIN, paused=True,
							 checkin=UTC0 + timedelta(seconds=h.t - 1000.0))]
		h.run(10)
		self.assertEqual((h.tauon.track_id, h.tauon.state, h.tauon.volume), (2, "playing", 0))
		self.assertTrue(h.mirror.engaged)
		self.assertLess(abs(h.tauon.progress - 10_000), 2500)

		# A real pause later on is still followed
		h.jf.list = [session(item="b", path="/music/X/b.flac", title="Song B", duration_ms=3 * MIN,
							 position_ms=30_000, paused=True)]
		h.run(6)
		self.assertEqual((h.tauon.state, h.tauon.volume), ("paused", 70))

	def test_finamp_track_end_pattern(self):
		h = Harness(self)
		h.jf.list = [session(position_ms=60_000)]
		h.run(6)
		# The track ends: reported paused at 0, and the next one comes late (battery saving)
		h.tauon.commands.clear()
		h.jf.list = [session(paused=True)]
		h.run(120)
		self.assertEqual((h.tauon.track_id, h.tauon.state, h.tauon.volume), (1, "playing", 0))
		self.assertTrue(h.mirror.engaged)
		self.assertEqual(h.tauon.commands, [])
		# The next track shows up paused at ~3 s, then plays
		h.jf.list = [session(item="b", path="/music/X/b.flac", title="Song B", duration_ms=3 * MIN,
							 position_ms=2990, paused=True, checkin=UTC0 + timedelta(seconds=h.t - 1000.0))]
		h.run(1)
		h.jf.list = [session(item="b", path="/music/X/b.flac", title="Song B", duration_ms=3 * MIN,
							 position_ms=4000, checkin=UTC0 + timedelta(seconds=h.t - 1000.0))]
		h.run(5)
		self.assertEqual((h.tauon.track_id, h.tauon.state, h.tauon.volume), (2, "playing", 0))
		self.assertLess(abs(h.tauon.progress - 9000), 2500)

	def test_track_end_without_a_next_track_hands_back(self):
		h = Harness(self)
		h.jf.list = [session(position_ms=60_000)]
		h.run(6)
		h.jf.list = [session(paused=True)]
		h.run(tm.SWITCH_HOLD + 10)
		self.assertEqual((h.tauon.state, h.tauon.volume), ("paused", 70))
		self.assertFalse(h.mirror.engaged)

	def test_ignores_stale_report_after_quick_skip(self):
		h = Harness(self)
		h.jf.list = [session(checkin=UTC0)]
		h.run(10)
		switch = UTC0 + timedelta(seconds=h.t - 1000.0)
		h.jf.list = [session(item="b", path="/music/X/b.flac", title="Song B", duration_ms=3 * MIN, checkin=switch)]
		h.run(1)
		# As seen from Finamp: the old track again, with the new track's position and no new check-in
		for i in range(1, 20):
			h.jf.list = [session(position_ms=i * 1000, checkin=switch)]
			h.step(1.0)
		self.assertEqual((h.tauon.track_id, h.tauon.state), (2, "playing"))
		self.assertLess(abs(h.tauon.progress - 20_000), 2500)
		# Its loop restarts it: a real report, taken as usual
		h.jf.list = [session(item="b", path="/music/X/b.flac", title="Song B", duration_ms=3 * MIN,
							 checkin=switch + timedelta(seconds=30))]
		h.run(3)
		self.assertEqual(h.tauon.track_id, 2)
		self.assertLess(abs(h.tauon.progress - 3000), 1000)

	def test_keeps_retrying_a_track_tauon_wont_switch_to(self):
		h = Harness(self)
		real_start = h.tauon.start
		h.tauon.start = lambda pid, pos: h.tauon._command("start", pid, pos)  # Tauon ignores it
		h.jf.list = [session()]
		h.run(25)
		self.assertEqual([c[0] for c in h.tauon.commands].count("start"), tm.MAX_START_TRIES)  # then rests
		h.tauon.start = real_start  # e.g. the stream loads now
		h.run(10)
		self.assertEqual((h.tauon.track_id, h.tauon.state), (1, "playing"))

	def test_pause_at_start_of_same_track_is_a_pause(self):
		h = Harness(self)
		h.jf.list = [session()]
		h.step()
		h.jf.list = [session(paused=True)]  # within its first second, so not a track end
		h.run(8)
		self.assertEqual((h.tauon.state, h.tauon.volume), ("paused", 70))

	def test_phone_stops(self):
		h = Harness(self)
		h.jf.list = [session()]
		h.run(5)
		h.jf.list = []
		h.run(2)
		self.assertEqual(h.tauon.state, "playing")  # grace period
		h.run(4)
		self.assertEqual((h.tauon.state, h.tauon.volume), ("paused", 70))
		self.assertFalse(h.mirror.engaged)

	def test_phone_vanishes_mid_track(self):
		h = Harness(self)
		h.jf.list = [session(position_ms=3 * MIN)]
		h.run(5)
		h.run(85)  # still "playing" a 4 min track at 4:20+...
		self.assertEqual((h.tauon.state, h.tauon.volume), ("paused", 70))

	def test_missing_track_pauses_tauon(self):
		h = Harness(self)
		h.tauon.state, h.tauon.track_id = "playing", 3
		h.jf.list = [session(item="zz", path="/music/unknown.flac", title="Nope")]
		h.run(3)
		self.assertEqual(h.tauon.state, "paused")
		self.assertNotIn("start", [c[0] for c in h.tauon.commands])

	def test_repeat_one_keeps_working(self):
		h = Harness(self)
		for _ in range(5):
			# Same item, same position 0: only the check-in time says it's a new play
			h.jf.list = [session(checkin=UTC0 + timedelta(seconds=h.t - 1000.0))]
			h.run(6)
			self.assertEqual((h.tauon.track_id, h.tauon.state), (1, "playing"))
			h.tauon.track_id, h.tauon.progress = 2, 0  # Tauon ran on into its next track
		self.assertEqual([c[0] for c in h.tauon.commands].count("start"), 5)

	def test_no_seek_right_before_the_end(self):
		h = Harness(self)
		h.jf.list = [session(position_ms=4 * MIN - 2500)]
		h.run(6)
		self.assertEqual(h.tauon.track_id, 1)
		self.assertNotIn("seek", [c[0] for c in h.tauon.commands])

	def test_recovers_volume_after_crash(self):
		h = Harness(self, volume=0, state={"volume": 55})
		h.run(1)
		self.assertEqual(h.tauon.volume, 55)
		self.assertEqual(h.tauon.state, "stopped")
		self.assertFalse(h.state_file.exists())

	def test_crash_recovery_respects_a_new_volume(self):
		h = Harness(self, volume=30, state={"volume": 55})
		h.run(1)
		self.assertEqual(h.tauon.volume, 30)
		self.assertFalse(h.state_file.exists())

	def test_crash_recovery_while_phone_plays_keeps_the_old_volume(self):
		h = Harness(self, volume=0, state={"volume": 55})
		h.jf.list = [session()]
		h.run(4)
		self.assertEqual(h.tauon.volume, 0)
		h.jf.list = []
		h.run(6)
		self.assertEqual(h.tauon.volume, 55)

	def test_shutdown_pauses_before_unmuting(self):
		h = Harness(self)
		h.jf.list = [session()]
		h.run(4)
		gap, tm.COMMAND_GAP = tm.COMMAND_GAP, 0
		self.addCleanup(setattr, tm, "COMMAND_GAP", gap)
		h.tauon.strict = False  # the real shutdown sleeps COMMAND_GAP between the two
		h.mirror.shutdown()
		self.assertEqual([c[0] for c in h.tauon.commands][-2:], ["pause", "volume"])
		self.assertEqual((h.tauon.state, h.tauon.volume), ("paused", 70))

	def test_no_mute(self):
		h = Harness(self)
		h.mirror.volume.enabled = False
		h.jf.list = [session()]
		h.run(4)
		self.assertEqual(h.tauon.volume, 70)
		self.assertEqual(h.tauon.commands[0][0], "start")


class NudgerTest(unittest.TestCase):
	def setUp(self):
		self.jf = FakeJellyfin()
		self.nudger = tm.Nudger(self.jf, True)

	def feed(self, t, item, **kw):
		self.nudger.tick(session(item=item, **kw), t)

	def test_nudges_once_after_the_last_of_quick_skips(self):
		self.feed(0.0, "a")
		self.feed(5.0, "a")
		self.assertEqual(self.jf.commands, [])  # no change, no nudge
		for i, item in enumerate("bcd"):
			self.feed(10.0 + i, item)
		self.feed(13.0, "d")
		self.assertEqual(self.jf.commands, [])
		self.feed(12.0 + tm.NUDGE_AFTER, "d")
		self.assertEqual(self.jf.commands, [("s1", "SetVolume", {"Volume": "99"}), ("s1", "SetVolume", {"Volume": "100"})])
		self.feed(30.0, "d")
		self.assertEqual(len(self.jf.commands), 2)

	def test_stale_report_also_counts_as_a_change(self):
		self.feed(0.0, "a")
		self.feed(1.0, "b")
		self.feed(2.0, "a")  # the server shows the previous song again
		self.feed(2.0 + tm.NUDGE_AFTER, "a")
		self.assertEqual(len(self.jf.commands), 2)

	def test_restores_the_real_volume_if_the_phone_still_shows_the_nudge(self):
		self.nudger.restore = (79, 80)
		s = session(item="a")
		s["PlayState"]["VolumeLevel"] = 79
		self.nudger.tick(s, 0.0)
		s = session(item="b")
		s["PlayState"]["VolumeLevel"] = 79
		self.nudger.tick(s, 1.0)
		self.nudger.tick(s, 1.0 + tm.NUDGE_AFTER)
		self.assertEqual([c[2]["Volume"] for c in self.jf.commands], ["79", "80"])

	def test_volume_zero_nudges_up(self):
		s = session(item="a")
		s["PlayState"]["VolumeLevel"] = 0
		self.nudger.tick(s, 0.0)
		s = dict(s, NowPlayingItem=dict(s["NowPlayingItem"], Id="b"))
		self.nudger.tick(s, 1.0)
		self.nudger.tick(s, 1.0 + tm.NUDGE_AFTER)
		self.assertEqual([c[2]["Volume"] for c in self.jf.commands], ["1", "0"])

	def test_skips_devices_without_remote_volume_and_gives_up_after_a_refusal(self):
		s = session(item="a")
		s["SupportedCommands"] = []
		self.nudger.tick(s, 0.0)
		s = dict(s, NowPlayingItem=dict(s["NowPlayingItem"], Id="b"))
		self.nudger.tick(s, 1.0)
		self.nudger.tick(s, 5.0)
		self.assertEqual(self.jf.commands, [])

		self.jf.fail = True
		self.feed(10.0, "a")
		with self.assertLogs(tm.log, "WARNING"):
			self.feed(20.0, "a")
		self.jf.fail = False
		self.feed(21.0, "b")
		self.feed(30.0, "b")
		self.assertEqual(self.jf.commands, [])

	def test_disabled(self):
		nudger = tm.Nudger(self.jf, False)
		nudger.tick(session(item="a"), 0.0)
		nudger.tick(session(item="b"), 1.0)
		nudger.tick(session(item="b"), 9.0)
		self.assertEqual(self.jf.commands, [])

	def test_mirror_nudges_the_followed_phone(self):
		h = Harness(self)
		h.jf.list = [session(item="a")]
		h.run(3)
		h.jf.list = [session(item="b", path="/music/X/b.flac", title="Song B", checkin=UTC0 + timedelta(seconds=3))]
		h.run(4)
		self.assertEqual([c[1] for c in h.jf.commands], ["SetVolume", "SetVolume"])


if __name__ == "__main__":
	unittest.main()
