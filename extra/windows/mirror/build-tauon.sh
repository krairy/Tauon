#!/usr/bin/env bash
# Builds Tauon from this source checkout into .venv, the way run.sh and the
# Windows CI job do. Runs in an MSYS2 CLANG64 shell; tauon-mirror.ps1 calls it.
# Steps that are already done are skipped, so reruns take seconds.
#
#   build-tauon.sh [--force]    --force rebuilds the dependencies and native code

set -euo pipefail

force=0
[[ "${1-}" == "--force" ]] && force=1

say() { printf '  %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
[[ -f pyproject.toml && -f src/tauon/__main__.py ]] || fail "This script must stay inside a Tauon source checkout."
[[ "${MSYSTEM-}" == "CLANG64" ]] || fail "Run this in the MSYS2 CLANG64 shell (MSYSTEM is '${MSYSTEM-}')."

stamps=build/windows-mirror
mkdir -p "${stamps}"

# 1. MSYS2 packages from extra/msyspac.txt
mapfile -t packages < <(tr -d '\r' < extra/msyspac.txt | sed '/^\s*$/d')
pacman_log="${stamps}/pacman.log"
run_pacman() {  # pacman's output goes to the screen and to pacman_log
	printf '\n$ pacman %s\n' "$*" >> "${pacman_log}"
	pacman --noconfirm --disable-download-timeout "$@" 2>&1 | tee -a "${pacman_log}"
}
if ! pacman -Q "${packages[@]}" > /dev/null 2>&1; then
	say "Installing the build tools and libraries Tauon needs (MSYS2 packages)."
	say "This downloads about 1-2 GB the first time and can take a while."
	: > "${pacman_log}"
	free_gb=$(( $(df -Pk / | awk 'NR==2 {print $4}') / 1024 / 1024 ))
	(( free_gb >= 6 )) || fail "Only ${free_gb} GB free on the MSYS2 drive; installing and building needs about 6 GB. Free some space and run this script again."
	# Left behind by an interrupted pacman; blocks every later run
	if [[ -f /var/lib/pacman/db.lck ]] && ! ps -W 2> /dev/null | grep -qi 'pacman\.exe'; then
		say "Removing a lock left by an interrupted install."
		rm -f /var/lib/pacman/db.lck
	fi
	# A partial upgrade can break MSYS2, so update everything first. The
	# first update of a fresh MSYS2 may close this window: then start again.
	echo update > "${stamps}/pacman.stage"
	run_pacman -Syu || fail "Updating MSYS2 failed. Close all MSYS2 windows and run this script again. Details: $(cygpath -w "${pacman_log}")"
	rm -f "${stamps}/pacman.stage"
	# --ask=4: with --noconfirm pacman answers "No" to "X conflicts with Y,
	# remove Y?" and gives up; this answers "Yes".
	# --overwrite: files an interrupted install left behind would otherwise
	# stop every retry with "exists in filesystem"
	if ! run_pacman -S --needed --ask=4 --overwrite '*' "${packages[@]}"; then
		say "Retrying with a fresh package list."
		run_pacman -Syy > /dev/null
		run_pacman -S --needed --ask=4 --overwrite '*' "${packages[@]}" \
			|| fail "Installing MSYS2 packages failed. The lines starting with 'error:' above say why; they are also in $(cygpath -w "${pacman_log}")"
	fi
	# Downloaded packages stay in pacman's cache (~1-2 GB); not needed now
	pacman -Scc --noconfirm > /dev/null 2>&1 || true
else
	say "MSYS2 packages: OK"
fi

python -c 'import sys; sys.exit(sys.version_info < (3, 13))' \
	|| fail "Tauon needs Python 3.13 or newer from MSYS2 (found $(python --version 2>&1)). Open 'MSYS2 CLANG64' and run: pacman -Syu"

# 2. Virtual environment
if [[ ! -x .venv/bin/python.exe && ! -x .venv/bin/python ]]; then
	if [[ -d .venv ]]; then
		fail "The folder .venv exists but was not made by MSYS2's Python (no .venv/bin/python). Rename or delete it, then run this again."
	fi
	say "Creating the Python environment (.venv)."
	python -m venv .venv
	rm -f "${stamps}"/*.done
fi
# shellcheck disable=SC1091
source .venv/bin/activate

export CFLAGS="-I${MSYSTEM_PREFIX}/include"
export CMAKE_PREFIX_PATH="${MSYSTEM_PREFIX}"

newer_than() {  # newer_than STAMP PATH...: true if any file under PATH changed since STAMP
	[[ ! -f "$1" ]] && return 0
	local stamp="$1"
	shift
	[[ -n "$(find "$@" -type f -newer "${stamp}" -print -quit 2> /dev/null)" ]]
}

# 3. Python dependencies, as run.sh's clean run installs them
if (( force )) || newer_than "${stamps}/deps.done" pyproject.toml extra/msyspac.txt; then
	say "Installing Python packages (first time: several minutes)."
	python -m pip install --upgrade pip scikit-build-core
	python -c 'import tomllib; p = tomllib.load(open("pyproject.toml", "rb"))["project"]; print("\n".join(p["dependencies"] + p["optional-dependencies"]["all"] + p["optional-dependencies"]["dev"]))' > "${stamps}/requirements.txt"
	python -m pip install -r "${stamps}/requirements.txt"
	touch "${stamps}/deps.done"
else
	say "Python packages: OK"
fi

# 4. Editable install: Python changes apply on the next launch, native code
# (launcher, Phazor, translations) is rebuilt incrementally in build/editable
launcher=.venv/bin/tauon.exe
if (( force )) || [[ ! -f "${launcher}" ]] \
	|| newer_than "${stamps}/native.done" CMakeLists.txt pyproject.toml src/tauon_native src/phazor locale; then
	say "Building Tauon (first time: several minutes)."
	python -m pip install --no-build-isolation -C build-dir=build/editable -e . --no-deps
	[[ -f "${launcher}" ]] || fail "The build finished but ${launcher} is missing."
	touch "${stamps}/native.done"
else
	say "Tauon build: up to date"
fi
