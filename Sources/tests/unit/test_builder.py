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


def manifest(pkg, data):
    (pkg / "NEMESIS.data").write_bytes(data.encode() if isinstance(data, str) else data)


@pytest.mark.parametrize(
    "body, warning",
    [
        ("version=1.1\nid=my.tool\ntypes=a\npackage-version=1.0\nshort-desc=s\n", "uses type=, not types="),
        ("version=1.1\nid=my.tool\npackage-version=1.0\nshort-desc=s\n", "no type= given"),
        ("version=1.1\nid=my.tool\ntype=a\nshort-desc=s\n", "package-version= is not set"),
        ("version=1.1\nid=my.tool\ntype=a\npackage-version=1.0\n", "short-desc= is not set"),
        ("version=1.0\nid=my.tool\nos=any\n", "1.1 keys present"),
    ],
)
def test_incomplete_descriptors_warn_and_still_build(pkg, tmp_path, body, warning):
    """Given a descriptor the unit would accept but which is missing what it should have
    When the builder runs on it
    Then it warns and writes the zip
    """
    manifest(pkg, f"[nemesis]\n{body}")
    res = build(pkg, "-o", tmp_path / "o.zip")
    assert res.returncode == 0, res.stderr
    assert warning in res.stdout
    assert (tmp_path / "o.zip").exists()


def test_a_complete_descriptor_builds_without_warnings(pkg, tmp_path):
    manifest(pkg, "[nemesis]\nversion=1.1\nid=my.tool\ntype=a\npackage-version=1.0\nshort-desc=s\n")
    res = build(pkg, "-o", tmp_path / "o.zip")
    assert res.returncode == 0
    assert "warning" not in res.stdout


@pytest.mark.parametrize(
    "value",
    ['"a\\"b\\\\c"', '"two\nlines"', '"two\r\nlines"', '"lone\rreturn"', '"closed" trailing', "   plain  "],
)
def test_quoted_and_plain_values_parse_as_the_unit_reads_them(pkg, tmp_path, value):
    manifest(pkg, f"# comment\n; comment\n\r\nnot a pair\n[nemesis]\n  \nid=my.tool\nshort-desc={value}\n[other\n[other]\nid=ignored\n")
    assert build(pkg, "-o", tmp_path / "o.zip").returncode == 0


@pytest.mark.parametrize(
    "depends",
    ["other", "other@any", "other@1.2", "other@>=1.2", "other@<=1.2", "other@1.2-1.5", " a , b@1.0 ,"],
)
def test_valid_dependency_specs_build(pkg, tmp_path, depends):
    manifest(pkg, f"[nemesis]\nid=my.tool\ndepends={depends}\n")
    assert build(pkg, "-o", tmp_path / "o.zip").returncode == 0


@pytest.mark.parametrize("key, limit", [("short-desc", 64), ("detailed-desc", 256)])
def test_a_description_at_its_limit_builds(pkg, tmp_path, key, limit):
    manifest(pkg, f"[nemesis]\nid=my.tool\n{key}={'x' * limit}\n")
    assert build(pkg, "-o", tmp_path / "o.zip").returncode == 0
    manifest(pkg, f"[nemesis]\nid=my.tool\n{key}={'x' * (limit + 1)}\n")
    assert build(pkg, "-o", tmp_path / "o.zip").returncode != 0


@pytest.mark.parametrize(
    "newline, chars",
    [("\r\n", 1), ("\r", 1), ("\n", 1)],
    ids=["crlf", "lone-cr", "lf"],
)
def test_a_line_break_in_a_quoted_value_counts_as_one_character(pkg, tmp_path, newline, chars):
    """Given a quoted description that is exactly at the limit with its line break as one character
    When the builder measures it, as the unit does (ini.c:95)
    Then it builds
    """
    text = "x" * (32 - chars) + newline + "x" * 32
    manifest(pkg, f'[nemesis]\nid=my.tool\nshort-desc="{text}"\n')
    assert build(pkg, "-o", tmp_path / "o.zip").returncode == 0


def test_a_lone_return_ends_a_line(pkg, tmp_path):
    manifest(pkg, "[nemesis]\rid=my.tool\rname=N\r")
    assert build(pkg, "-o", tmp_path / "o.zip").returncode == 0


def test_a_section_line_with_no_closing_bracket_changes_nothing(pkg, tmp_path):
    manifest(pkg, "[nemesis]\n[broken\nid=my.tool\n")
    assert build(pkg, "-o", tmp_path / "o.zip").returncode == 0


def test_type_and_types_together_do_not_warn(pkg, tmp_path):
    manifest(pkg, "[nemesis]\nversion=1.1\nid=my.tool\ntype=a\ntypes=a\npackage-version=1.0\nshort-desc=s\n")
    assert "warning" not in build(pkg, "-o", tmp_path / "o.zip").stdout


def test_a_value_over_the_ini_limit_is_refused(pkg, tmp_path):
    manifest(pkg, "[nemesis]\nid=my.tool\nname=" + "x" * 1024 + "\n")
    res = build(pkg, "-o", tmp_path / "o.zip")
    assert res.returncode != 0
    assert "value too long" in res.stderr


def test_a_descriptor_that_is_not_utf8_is_refused(pkg, tmp_path):
    manifest(pkg, b"[nemesis]\nid=my.tool\nname=\xff\n")
    assert build(pkg, "-o", tmp_path / "o.zip").returncode != 0


def test_only_the_nemesis_section_counts(pkg, tmp_path):
    manifest(pkg, "[other]\nid=my.tool\n")
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
