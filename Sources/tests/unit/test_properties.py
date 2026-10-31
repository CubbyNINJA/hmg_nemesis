"""Seeded random properties, stdlib only. Set NEMESIS_PROP_SEED to replay a failure."""
from __future__ import annotations

import os
import random
import subprocess
import zipfile

import pytest

from helpers import PACKAGES, builder_cmd, known_bug
from sandbox import SH, Unit

SEED = int(os.environ.get("NEMESIS_PROP_SEED", "20261030"))
BP = "/system/build.prop"
ADB_KEYS = ["ro.adb.secure", "persist.service.adb.tcp.enable", "service.adb.tcp.port"]
SAFE_LINES = ["ro.a=1", "ro.adb.secure.more=1", "# comment", "", "x=a b  c", "back\\slash=1", "tab\tline", " lead", "star=*", "q='\"", "$HOME=`x`"]
needs_sh = pytest.mark.skipif(not SH, reason="needs a POSIX sh")


def build_prop(rng: random.Random) -> bytes:
    lines = [rng.choice(SAFE_LINES) for _ in range(rng.randint(1, 12))]
    for key in ADB_KEYS:
        if rng.random() < 0.5:
            lines.insert(rng.randint(0, len(lines)), f"{key}={rng.randint(0, 9)}")
    lines.insert(0, "ro.first=1")
    return ("\n".join(lines) + "\n").encode()


@needs_sh
@pytest.mark.parametrize("case", range(12))
def test_adb_install_then_uninstall_restores_any_build_prop(tmp_path, case):
    rng = random.Random(SEED + case)
    unit = Unit(tmp_path, PACKAGES / "adb-tcp")
    stock = build_prop(rng)
    unit.put(BP, stock)
    assert unit.hook("post").returncode == 0
    assert unit.hook("preun").returncode == 0
    assert unit.read(BP) == stock


@known_bug("G20", "restore rewrites build.prop line by line and ignores the full snapshot it took")
@needs_sh
@pytest.mark.parametrize(
    "stock",
    [b"ro.first=1\nro.adb.secure=1\nro.adb.secure=2\n", b"ro.first=1\nro.last=1"],
    ids=["duplicate-key", "no-trailing-newline"],
)
def test_uninstall_restores_unusual_build_prop_bytes(tmp_path, stock):
    # pkg_live_snapshot.sh: "The hooks snapshot whatever is on the live unit and restore those bytes."
    unit = Unit(tmp_path, PACKAGES / "adb-tcp")
    unit.put(BP, stock)
    assert unit.hook("post").returncode == 0
    assert unit.hook("preun").returncode == 0
    assert unit.read(BP) == stock


@needs_sh
def test_install_keeps_every_line_it_does_not_own(tmp_path):
    rng = random.Random(SEED)
    unit = Unit(tmp_path, PACKAGES / "adb-tcp")
    stock = build_prop(rng)
    unit.put(BP, stock)
    unit.hook("post")
    after = unit.read(BP).decode().split("\n")
    for line in stock.decode().split("\n"):
        if not any(line.startswith(key + "=") for key in ADB_KEYS):
            assert line in after


@pytest.mark.parametrize("case", range(15))
def test_builder_round_trips_any_tree(tmp_path, case):
    rng = random.Random(SEED + 100 + case)
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "NEMESIS.data").write_bytes(b"[nemesis]\nid=a.b\n")
    expected = {"NEMESIS.data": b"[nemesis]\nid=a.b\n"}
    for index in range(rng.randint(0, 12)):
        parts = [rng.choice(["bin", "svc", "files", "a b", "UP", "d.d"]) for _ in range(rng.randint(0, 3))]
        rel = "/".join(parts + [f"f{index}{rng.choice(['.sh', '.bin', '', '.xml'])}"])
        data = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 2048)))
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        expected[rel] = data
    out = tmp_path / "o.zip"
    subprocess.check_call(builder_cmd(root, "-o", out), stdout=subprocess.DEVNULL)
    with zipfile.ZipFile(out) as z:
        assert z.testzip() is None
        assert {n: z.read(n) for n in z.namelist()} == expected
