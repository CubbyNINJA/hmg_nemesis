"""make_nemesis_package.py. Requirements: 04-Packages.md section 5.2, AGENTS.md section 9."""
from __future__ import annotations

import subprocess
import zipfile

import pytest

from helpers import REPO, builder_cmd, known_bug


def build(pkg, *args):
    return subprocess.run(
        builder_cmd(pkg, *args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )


@pytest.fixture
def pkg(tmp_path):
    root = tmp_path / "my-tool"
    (root / "bin" / "deep").mkdir(parents=True)
    (root / "NEMESIS.data").write_bytes(b"[nemesis]\nid=my.tool\n[app.0]\nkind=sh\npath=bin/tool.sh\n")
    (root / "bin" / "tool.sh").write_bytes(b"#!/system/bin/sh\nexit 0\n")
    (root / "bin" / "deep" / "blob.bin").write_bytes(b"\x00\x01\r\n\xff")
    return root


def test_zips_every_file_with_paths_relative_to_the_package(pkg, tmp_path):
    out = tmp_path / "o.zip"
    assert build(pkg, "-o", out).returncode == 0
    assert sorted(zipfile.ZipFile(out).namelist()) == [
        "NEMESIS.data",
        "bin/deep/blob.bin",
        "bin/tool.sh",
    ]


def test_members_are_deflated_and_binary_files_keep_their_bytes(pkg, tmp_path):
    out = tmp_path / "o.zip"
    build(pkg, "-o", out)
    with zipfile.ZipFile(out) as z:
        assert z.getinfo("NEMESIS.data").compress_type == zipfile.ZIP_DEFLATED
        assert z.read("bin/deep/blob.bin") == b"\x00\x01\r\n\xff"


def test_default_output_sits_beside_the_package(pkg):
    assert build(pkg).returncode == 0
    assert (pkg.parent / "my-tool.zip").is_file()


def test_rebuilding_over_an_old_zip_replaces_it(pkg, tmp_path):
    """Given a zip from an earlier build that holds a file since removed
    When the builder writes to the same path
    Then the new zip does not contain the old file
    """
    out = tmp_path / "o.zip"
    with zipfile.ZipFile(out, "w") as old:
        old.writestr("removed-since", "stale")
    assert build(pkg, "-o", out).returncode == 0
    assert "removed-since" not in zipfile.ZipFile(out).namelist()


def test_missing_manifest_is_refused(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    res = build(empty, "-o", tmp_path / "o.zip")
    assert res.returncode != 0
    assert "missing" in res.stderr
    assert not (tmp_path / "o.zip").exists()


@known_bug("G1", "builder ships CRLF scripts unchanged")
def test_crlf_scripts_do_not_reach_the_unit(pkg, tmp_path):
    (pkg / "bin" / "tool.sh").write_bytes(b"#!/system/bin/sh\r\nexit 0\r\n")
    out = tmp_path / "o.zip"
    res = build(pkg, "-o", out)
    assert res.returncode != 0 or b"\r" not in zipfile.ZipFile(out).read("bin/tool.sh")


@known_bug("G1", "no .gitattributes pins LF, and core.autocrlf rewrites checkouts")
def test_gitattributes_pins_lf_for_unit_files():
    text = (REPO / ".gitattributes").read_text(encoding="utf-8")
    assert "*.sh text eol=lf" in text
    assert "NEMESIS.* text eol=lf" in text


@pytest.mark.parametrize("bad", ["../../traversal", ".hidden", "a b", "x" * 65])
def test_invalid_id_is_refused(pkg, tmp_path, bad):
    (pkg / "NEMESIS.data").write_text(f"[nemesis]\nid={bad}\n")
    assert build(pkg, "-o", tmp_path / "o.zip").returncode != 0


@known_bug("G18", "builder never checks that declared paths exist")
def test_declared_path_must_exist(pkg, tmp_path):
    (pkg / "bin" / "tool.sh").unlink()
    assert build(pkg, "-o", tmp_path / "o.zip").returncode != 0


@known_bug("G18", "output inside the package zips itself")
def test_output_inside_the_package_is_not_zipped_into_itself(pkg):
    out = pkg / "o.zip"
    assert build(pkg, "-o", out).returncode == 0
    assert "o.zip" not in zipfile.ZipFile(out).namelist()
