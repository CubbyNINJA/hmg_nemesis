#!/usr/bin/env python3
"""Sums up a drive-mode-discovery folder from the stick. Python 3 on your PC.

usage: python3 analyze.py <drive-mode-discovery-folder>

Prints what changed while the button was pressed, and writes the same to summary.txt in that folder.
Serial numbers, MAC addresses and VINs are masked, but read the summary before sharing it. The raw
files are not masked, and the log can hold phone and network names.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROP = re.compile(r"^\[(.+?)\]: \[(.*)\]\s*$")
LIKELY = re.compile(r"drive|mode|mdps|eco|sport|snow|normal|custom|profile", re.I)
# Words that often sit in unrelated names, so a match on one of these alone is not a lead.
NOISE = re.compile(r"airplane|model|modem|adb|usb|wifi|bluetooth|economy", re.I)
SECRET_KEY = re.compile(r"serial|imei|imsi|iccid|vin\b|\.vin|macaddr|mac_addr|bt\.addr|wifi\.addr|btaddr", re.I)
MAC = re.compile(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b")
VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
BUILD_INFO = ("ro.build.product", "ro.build.display.id", "ro.build.version.release", "ro.mobis.targetdir")
MAX_LOG_LINES = 150


def mask(text: str) -> str:
    return VIN.sub("<vin?>", MAC.sub("<mac>", text))


def read_props(path: Path) -> dict:
    out = {}
    for line in path.read_text(errors="replace").splitlines():
        found = PROP.match(line)
        if found:
            out[found.group(1)] = found.group(2)
    return out


def load_samples(folder: Path) -> list:
    times = {}
    index = folder / "times.txt"
    if index.exists():
        for line in index.read_text(errors="replace").splitlines():
            number, _, when = line.partition(" ")
            times[number] = when or number
    files = sorted((folder / "props").glob("*.txt"), key=lambda p: int(p.stem))
    return [(times.get(p.stem, p.stem), read_props(p)) for p in files]


def changes(samples: list) -> dict:
    """Each property that changed, with every value and when it took it."""
    found: dict = {}
    for (_, before), (when, after) in zip(samples, samples[1:]):
        for key in sorted(set(before) | set(after)):
            if before.get(key) != after.get(key):
                found.setdefault(key, [(samples[0][0], before.get(key, "(unset)"))]).append((when, after.get(key, "(unset)")))
    return found


def likely(name: str) -> bool:
    return bool(LIKELY.search(name)) and not NOISE.search(name)


def listing_changes(folder: Path, name: str) -> list:
    before, after = folder / f"before-{name}.txt", folder / f"after-{name}.txt"
    if not before.exists() or not after.exists():
        return []
    old = set(before.read_text(errors="replace").splitlines())
    return [mask(line) for line in after.read_text(errors="replace").splitlines() if line.strip() and line not in old]


def summarize(folder: Path) -> str:
    lines = [f"Summary of {folder.name}", ""]
    samples = load_samples(folder)
    if not samples:
        return "\n".join(lines + ["No property samples found. Did the capture finish?", ""])
    first = samples[0][1]
    lines += ["== Unit"] + [f"{key} = {mask(first[key])}" for key in BUILD_INFO if key in first]
    lines += [f"samples: {len(samples)}, from {samples[0][0]} to {samples[-1][0]}", ""]

    changed = {key: value for key, value in changes(samples).items() if not SECRET_KEY.search(key)}
    ordered = sorted(changed, key=lambda key: (not likely(key), key))
    lines.append(f"== Properties that changed ({len(changed)}). Likely names come first.")
    if not changed:
        lines.append("None. Either the button was not pressed during the capture, or the mode is not in a property.")
    for key in ordered:
        trail = ", ".join(f"{when}={mask(value)}" for when, value in changed[key][:12])
        lines.append(f"{'* ' if likely(key) else '  '}{key}: {trail}")
    names = sorted(key for key in first if likely(key) and not SECRET_KEY.search(key) and key not in changed)
    lines += ["", f"== Likely property names that did not change ({len(names)})"] + [f"{key} = {mask(first[key])}" for key in names]

    log = folder / "logcat.txt"
    matches = []
    if log.exists():
        matches = [mask(line) for line in log.read_text(errors="replace").splitlines() if likely(line)]
    lines += ["", f"== Log lines with likely words ({len(matches)}, showing up to {MAX_LOG_LINES})"] + matches[-MAX_LOG_LINES:]

    for name, title in (("property", "Stored properties"), ("data", "/data"), ("efs", "/efs"), ("sockets", "Sockets"), ("ps", "Processes")):
        lines += ["", f"== {title}: new or changed lines"] + (listing_changes(folder, name) or ["none"])
    before_ps = folder / "before-ps.txt"
    if before_ps.exists():
        vendor = [mask(line) for line in before_ps.read_text(errors="replace").splitlines() if re.search(r"micom|vehicle|\bcan\b|canbus|drive", line, re.I)]
        lines += ["", "== Processes that may talk to the vehicle (names only, nothing was contacted)"] + (vendor or ["none"])
    return "\n".join(lines) + "\n"


def main() -> int:
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        print(__doc__)
        return 1
    folder = Path(sys.argv[1])
    text = summarize(folder)
    (folder / "summary.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
