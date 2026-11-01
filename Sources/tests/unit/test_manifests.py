"""Shipped manifests, descriptors and scripts against 04-Packages.md, 05-Run-Scripts.md, AGENTS.md."""
from __future__ import annotations

import re

import pytest

from helpers import (
    DESCRIPTOR_DIRS,
    EXAMPLES,
    HOOK_KEYS,
    NATIVE,
    PACKAGE_DIRS,
    PACKAGES,
    id_ok,
    lf,
    manifest,
    usb_path_ok,
)

by_name = {"ids": lambda p: p.name}
SCRIPTS = sorted(p for d in PACKAGE_DIRS for p in d.rglob("*.sh"))
# Not on the unit's toolbox (AGENTS.md section 3).
FORBIDDEN = re.compile(r"(?<![\w./-])(awk|sed|busybox|unzip|curl|bash)(?![\w.-])")


def numbered(data: dict, prefix: str):
    return [v for k, v in data.items() if re.fullmatch(prefix + r"\.\d+", k)]


@pytest.mark.parametrize("value", ["a", "adb.tcp", "usb-handler", "a_b", "A9", "x" * 64])
def test_id_rule_accepts(value):
    assert id_ok(value)


@pytest.mark.parametrize("value", ["", ".a", "a..b", "a/b", "a b", "a|b", "x" * 65])
def test_id_rule_rejects(value):
    assert not id_ok(value)


@pytest.mark.parametrize("value", ["NEMESIS.sh", "scripts/diag.sh", "packages/my.tool.zip"])
def test_usb_path_rule_accepts(value):
    assert usb_path_ok(value)


@pytest.mark.parametrize(
    "value",
    ["", "/abs.sh", "\\abs.sh", "a/../b", "a;b", "a|b", "a&b", "a`b", "a$b", "a'b", 'a"b', "a\nb", "a(b", "a)b", "a<b", "a>b"],
)
def test_usb_path_rule_rejects(value):
    assert not usb_path_ok(value)


@pytest.mark.parametrize("pkg", PACKAGE_DIRS, **by_name)
def test_package_id_is_valid(pkg):
    assert id_ok(manifest(pkg)["nemesis"]["id"])


@pytest.mark.parametrize("pkg", PACKAGE_DIRS, **by_name)
def test_recommended_metadata_is_present(pkg):
    meta = manifest(pkg)["nemesis"]
    assert meta.get("name") and meta.get("version") and (meta.get("type") or meta.get("types"))


@pytest.mark.parametrize("pkg", PACKAGE_DIRS, **by_name)
def test_every_declared_path_exists(pkg):
    data = manifest(pkg)
    declared = [data["nemesis"][k] for k in HOOK_KEYS if k in data["nemesis"]]
    declared += [s["path"] for s in numbered(data, "app") + numbered(data, "service")]
    missing = [rel for rel in declared if not (pkg / rel).is_file()]
    assert not missing


@pytest.mark.parametrize("pkg", PACKAGE_DIRS, **by_name)
def test_section_values_are_documented(pkg):
    data = manifest(pkg)
    for app in numbered(data, "app"):
        assert app["kind"] in ("sh", "apk")
        assert app.get("uid", "root") in ("root", "system")
    for svc in numbered(data, "service"):
        assert svc["name"]
        assert svc.get("kind", "sh") in ("sh", "instance")
        assert svc.get("uid", "root") in ("root", "system")
        assert svc.get("mode") in ("restart", "background", "once")


def test_hello_examples_differ_only_by_service_mode():
    one = manifest(EXAMPLES / "package-hello")
    two = manifest(EXAMPLES / "package-hello-v2")
    assert one["service.0"].pop("mode") == "restart"
    assert two["service.0"].pop("mode") == "background"
    for meta in (one["nemesis"], two["nemesis"]):
        meta.pop("package-version")
        meta.pop("short-desc")
    assert one == two


@pytest.mark.parametrize("stick", DESCRIPTOR_DIRS, **by_name)
def test_descriptor_type_and_path(stick):
    meta = manifest(stick)["nemesis"]
    assert meta["type"] in ("execute", "install", "open")
    if meta["type"] != "open":
        assert usb_path_ok(meta["path"])


@pytest.mark.parametrize("stick", DESCRIPTOR_DIRS, **by_name)
def test_execute_descriptor_points_at_a_real_script(stick):
    meta = manifest(stick)["nemesis"]
    if meta["type"] == "execute":
        assert (stick / meta["path"]).is_file()


@pytest.mark.parametrize(
    "stick,source,name",
    [
        ("usb-stick-execute", "execute-hello", "NEMESIS.data"),
        ("usb-stick-execute", "execute-hello", "NEMESIS.sh"),
        ("usb-stick-open", "open-manager", "NEMESIS.data"),
    ],
)
def test_stick_copies_match_their_source(stick, source, name):
    # Sources/Makefile `examples` target copies these.
    assert lf((EXAMPLES / stick / name).read_bytes()) == lf((EXAMPLES / source / name).read_bytes())


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: "/".join(p.parts[-3:]))
def test_script_uses_the_unit_shell(script):
    assert script.read_text(encoding="utf-8").splitlines()[0] == "#!/system/bin/sh"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: "/".join(p.parts[-3:]))
def test_script_uses_no_host_only_tools(script):
    for line in script.read_text(encoding="utf-8").splitlines():
        if not line.lstrip().startswith("#"):
            assert not FORBIDDEN.search(line), line


@pytest.mark.parametrize("pkg", ["adb-tcp", "wireless-projection", "projection-tbt"])
def test_live_file_package_backs_up_under_its_own_id(pkg):
    # AGENTS.md section 4.6, backup/restore rule.
    root = PACKAGES / pkg
    common = (root / "scripts" / "common.sh").read_text(encoding="utf-8")
    assert f"BACKUP_DIR=/data/nemesis/backup/{manifest(root)['nemesis']['id']}\n" in common
    assert "/data/local/tmp" not in common


@pytest.mark.parametrize("pkg", ["adb-tcp", "wireless-projection", "projection-tbt"])
def test_backup_is_removed_only_after_a_verified_restore(pkg):
    text = (PACKAGES / pkg / "scripts" / "preun.sh").read_text(encoding="utf-8")
    restore = text.index("restore_all")
    assert "exit 1" in text[restore : text.index("remove_backup_dir")]
    assert restore < text.index("remove_backup_dir")


def c_string_block(source: str, name: str) -> str:
    block = re.search(r"\b%s\s*=\s*(.*?);" % name, source, re.S).group(1)
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', block)
    return "".join(parts).replace('\\"', '"').replace("\\n", "\n")


def test_embedded_usb_handler_matches_the_package_source():
    source = (NATIVE / "pkg.c").read_text(encoding="utf-8")
    pkg = PACKAGES / "usb-handler"
    version = re.search(r'#define NEMESIS_VERSION "([^"]+)"', (NATIVE / "nemesis.h").read_text(encoding="utf-8")).group(1)
    embedded = c_string_block(source, "USB_HANDLER_META").replace("package-version=\n", f"package-version={version}\n")
    assert embedded == (pkg / "NEMESIS.data").read_text(encoding="utf-8")
    shipped = (pkg / "svc" / "usb-handler.sh").read_text(encoding="utf-8")
    assert c_string_block(source, "USB_HANDLER_SH").split() == shipped.split()
