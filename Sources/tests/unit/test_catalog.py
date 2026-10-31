"""Packages/ catalog zips against Sources/packages. Requirement: AGENTS.md section 6."""
from __future__ import annotations

import io
import re
import zipfile

import pytest

from helpers import PACKAGES, REPO, id_ok, lf, manifest, read_ini, usb_path_ok

CATALOG = sorted((REPO / "Packages").glob("*.zip"))
SOURCE_BY_ID = {manifest(p.parent)["nemesis"]["id"]: p.parent for p in PACKAGES.glob("*/NEMESIS.data")}
by_stem = {"ids": lambda p: p.stem}


def descriptor(path) -> dict:
    with zipfile.ZipFile(path) as z:
        return read_ini(z.read("NEMESIS.data").decode("utf-8"))["nemesis"]


def inner(path) -> zipfile.ZipFile:
    with zipfile.ZipFile(path) as z:
        return zipfile.ZipFile(io.BytesIO(z.read(descriptor(path)["path"])))


def test_catalog_is_not_empty():
    assert CATALOG


@pytest.mark.parametrize("path", CATALOG, **by_stem)
def test_zip_is_a_stick_root_with_an_install_descriptor(path):
    meta = descriptor(path)
    assert meta["type"] == "install"
    assert usb_path_ok(meta["path"])
    with zipfile.ZipFile(path) as z:
        assert sorted(z.namelist()) == sorted(["NEMESIS.data", meta["path"]])


@pytest.mark.parametrize("path", CATALOG, **by_stem)
def test_inner_package_has_a_valid_manifest_at_its_root(path):
    meta = read_ini(inner(path).read("NEMESIS.data").decode("utf-8"))["nemesis"]
    assert id_ok(meta["id"])
    assert descriptor(path)["path"] == f"packages/{meta['id']}.zip"


@pytest.mark.parametrize("path", CATALOG, **by_stem)
def test_inner_package_matches_its_source(path):
    # AGENTS.md section 6: a changed source means a rebuilt catalog zip.
    z = inner(path)
    src = SOURCE_BY_ID[read_ini(z.read("NEMESIS.data").decode("utf-8"))["nemesis"]["id"]]
    tree = {p.relative_to(src).as_posix(): p for p in src.rglob("*") if p.is_file()}
    assert sorted(z.namelist()) == sorted(tree)
    changed = [n for n in tree if lf(z.read(n)) != lf(tree[n].read_bytes())]
    assert not changed


@pytest.mark.parametrize("path", CATALOG, **by_stem)
def test_inner_scripts_are_lf(path):
    z = inner(path)
    crlf = [n for n in z.namelist() if n.endswith((".sh", ".data")) and b"\r" in z.read(n)]
    assert not crlf


def test_readme_lists_every_catalog_zip_with_a_real_source():
    text = (REPO / "Packages" / "README.md").read_text(encoding="utf-8")
    table = text.split("## NEMESIS Catalog", 1)[1].split("## Community Catalog", 1)[0]
    rows = {r.split("|")[1].strip(): r for r in table.splitlines() if r.startswith("| ") and "../Sources" in r}
    assert sorted(rows) == sorted(p.stem for p in CATALOG)
    for row in rows.values():
        link = re.search(r"\(\.\./(Sources/packages/[\w-]+)\)", row).group(1)
        assert (REPO / link / "NEMESIS.data").is_file()
