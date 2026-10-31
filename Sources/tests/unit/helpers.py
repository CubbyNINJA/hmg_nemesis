"""Shared paths and readers for the host unit tests."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SOURCES = REPO / "Sources"
PACKAGES = SOURCES / "packages"
EXAMPLES = SOURCES / "examples"
NATIVE = SOURCES / "native"
BUILDER = SOURCES / "tools" / "make_nemesis_package.py"

HOOK_KEYS = ("preinstall", "postinstall", "preuninstall", "postuninstall")
PACKAGE_DIRS = sorted(p.parent for p in PACKAGES.glob("*/NEMESIS.data")) + [
    EXAMPLES / "package-hello",
    EXAMPLES / "package-hello-v2",
]
DESCRIPTOR_DIRS = sorted(
    p.parent
    for p in list(EXAMPLES.glob("*/NEMESIS.data")) + [SOURCES / "usb_bundle/open-manager/NEMESIS.data"]
    if not p.parent.name.startswith("package-")
)


def builder_cmd(*args) -> list:
    """The builder runs in a subprocess, so coverage has to be started inside it."""
    prefix = ["-m", "coverage", "run"] if os.environ.get("NEMESIS_COVERAGE") else []
    return [sys.executable, *prefix, str(BUILDER), *map(str, args)]


def known_bug(gap: str, why: str):
    """Asserts the correct behaviour. Strict: a fix fails the suite until this marker goes."""
    return pytest.mark.xfail(reason=f"{gap}: {why}")


def read_ini(text: str) -> dict:
    """Same rules as native/ini.c: trim, # and ; comments, the first duplicate key wins."""
    out: dict = {}
    section = ""
    for raw in text.split("\n"):
        line = raw.strip(" \t\r")
        if not line or line[0] in "#;":
            continue
        if line[0] == "[":
            if "]" in line:
                section = line[1 : line.index("]")].strip(" \t\r")
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            out.setdefault(section, {}).setdefault(key.strip(" \t\r"), val.strip(" \t\r"))
    return out


def manifest(pkg_dir: Path) -> dict:
    return read_ini((pkg_dir / "NEMESIS.data").read_text(encoding="utf-8"))


def id_ok(value: str) -> bool:
    """04-Packages.md section 2, same rules as pkg_id_ok in native/pkg.c."""
    return (
        0 < len(value) <= 64
        and not value.startswith(".")
        and ".." not in value
        and re.fullmatch(r"[A-Za-z0-9._-]+", value) is not None
    )


def usb_path_ok(rel: str) -> bool:
    """05-Run-Scripts.md section 2, same rules as rel_ok in native/usb.c."""
    return (
        bool(rel)
        and rel[0] not in "/\\"
        and ".." not in rel
        and not set(rel) & set(";|&`$'\"\n\r()<>")
    )


def lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def doc(name: str) -> str:
    """Document text with whitespace collapsed, so assertions survive reflowing."""
    return " ".join((REPO / name).read_text(encoding="utf-8").split())
