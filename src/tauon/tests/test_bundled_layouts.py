"""Bundled layouts are added to the user's own custom layouts once."""

from __future__ import annotations

import builtins
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

if not hasattr(builtins, "_"):
	builtins._ = lambda s: s

from tauon.t_modules import t_custom

ASSETS = Path(t_custom.__file__).resolve().parent.parent / "assets"
NAME = "Large art and song title"


def make(user: Path) -> t_custom.CustomLayout:
	cl = object.__new__(t_custom.CustomLayout)
	cl.tauon = SimpleNamespace(user_directory=user, dirs=SimpleNamespace(asset_directory=ASSETS))
	cl.slots, cl.slot_names, cl.slot_columns = [None], [None], [None]
	cl.active_slot = 0
	cl.layout_order = []
	cl._loaded = False
	cl._save_ok = False
	cl._written = {}
	cl._order_written = None
	cl.bundled_installed = []
	cl._columns_owner = None
	# Templates need the GUI; blank trees stand in for them
	cl.template = lambda name: None
	return cl


def load(user: Path) -> list[str]:
	cl = make(user)
	cl.load_slots()
	assert cl._save_ok
	return [cl.slot_title(i) for i in range(len(cl.slots))]


@pytest.fixture
def user(tmp_path: Path) -> Path:
	assert (ASSETS / t_custom.BUNDLED_LAYOUTS_DIR / f"{NAME}{t_custom.LAYOUT_EXT}").is_file()
	return tmp_path


def test_fresh_user_gets_bundled_layout_once(user: Path) -> None:
	assert NAME in load(user)
	assert load(user).count(NAME) == 1
	order = json.loads((user / "layouts" / "order.json").read_text())
	assert order["bundled"] == [NAME]
	assert f"layout:{NAME}" in order["order"]


def test_deleted_bundled_layout_stays_deleted(user: Path) -> None:
	load(user)
	(user / "layouts" / f"{NAME}.tlayout").unlink()
	assert NAME not in load(user)


def test_added_after_legacy_migration(user: Path) -> None:
	(user / "custom_layouts.json").write_text(json.dumps({"0": None, "names": {"0": "Mine"}, "active": 0}))
	assert load(user) == ["Mine", NAME]


def test_existing_folder_without_it_gets_it(user: Path) -> None:
	(user / "layouts").mkdir()
	(user / "layouts" / "order.json").write_text(json.dumps({"order": [], "active": "Mine"}))
	(user / "layouts" / "Mine.tlayout").write_text(json.dumps({"format": "tauon-layout", "version": 1, "name": "Mine", "tree": None}))
	assert load(user) == ["Mine", NAME]


def test_same_name_already_present_is_not_duplicated(user: Path) -> None:
	(user / "layouts").mkdir()
	shutil.copy(ASSETS / "layouts" / f"{NAME}.tlayout", user / "layouts")
	assert load(user) == [NAME]
	order = json.loads((user / "layouts" / "order.json").read_text())
	assert order["bundled"] == [NAME]
