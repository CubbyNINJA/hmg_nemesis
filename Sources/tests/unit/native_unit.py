"""Builds the real CLI for the host and drives it against a fake unit in a temp directory."""
from __future__ import annotations

import io
import os
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from helpers import NATIVE

HERE = Path(__file__).resolve().parent
SOURCES = ["main.c", "util.c", "ini.c", "meta.c", "update.c", "zip.c", "pkg.c", "svc.c", "usb.c", "su.c"]
CC = os.environ.get("CC", "gcc")


def compiler_available() -> bool:
    return os.name == "posix" and shutil.which(CC) is not None and Path("/usr/include/zlib.h").exists()


def build(root: Path, out_dir: Path) -> Path:
    flags = ["-std=gnu11", "-O0", "-g", "-D_GNU_SOURCE", "-D_FILE_OFFSET_BITS=64", f'-DTEST_ROOT="{root}"', "-w"]
    if os.environ.get("NEMESIS_COVERAGE"):
        flags.append("--coverage")
    if os.environ.get("NEMESIS_SANITIZE"):
        flags.extend(["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-omit-frame-pointer"])
    objects = []
    includes = [f"-I{HERE / 'native' / 'stubs'}", f"-I{NATIVE}"]
    for name in SOURCES:
        obj = out_dir / (name[:-2] + ".o")
        subprocess.check_call([CC, *flags, *includes, "-include", str(HERE / "native" / "shim.h"), "-c", str(NATIVE / name), "-o", str(obj)])
        objects.append(str(obj))
    shim = out_dir / "shim.o"
    subprocess.check_call([CC, *flags, "-c", str(HERE / "native" / "shim.c"), "-o", str(shim)])
    binary = out_dir / "nemesis"
    subprocess.check_call([CC, *flags, *objects, str(shim), "-lz", "-o", str(binary)])
    return binary


def executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("#!/bin/sh\n" + body).encode())
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def make_zip(path: Path, members: dict, method: int = zipfile.ZIP_DEFLATED) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", method) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


class FakeUnit:
    def __init__(self, root: Path, binary: Path):
        self.root = root
        self.binary = binary
        self.workdir = root / "work"
        self.reset()

    def reset(self) -> None:
        self.kill_all()
        for child in self.root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        for rel in ["data/nemesis", "data/local/tmp", "system/xbin", "system/bin", "system/etc", "system/app", "dev", "props", "storage/usb0", "work"]:
            (self.root / rel).mkdir(parents=True, exist_ok=True)
        (self.root / "proc_mounts").write_text("")
        shutil.copy(str(self.binary), str(self.cli))
        self.android(up=True)
        executable(self.bin("am"), f'echo "am $*" >> "{self.root}/calls"\n')
        executable(self.bin("monkey"), f'echo "monkey $*" >> "{self.root}/calls"\n')
        executable(self.bin("reboot"), f'echo "reboot $*" >> "{self.root}/calls"\n')
        self.pm_script('echo Success\n')

    @property
    def cli(self) -> Path:
        return self.root / "system" / "xbin" / "nemesis"

    def bin(self, name: str) -> Path:
        return self.root / "system" / "bin" / name

    def path(self, rel: str) -> Path:
        return self.root / rel.lstrip("/")

    def android(self, up: bool) -> None:
        (self.root / "props" / "sys.boot_completed").write_text("1" if up else "0")

    def prop(self, name: str) -> str:
        target = self.root / "props" / name
        return target.read_text() if target.exists() else ""

    def pm_script(self, body: str) -> None:
        log = f'echo "pm $* boot=$(cat "{self.root}/props/sys.boot_completed")" >> "{self.root}/calls"\n'
        executable(self.bin("pm"), log + body)

    def calls(self) -> str:
        target = self.root / "calls"
        return target.read_text() if target.exists() else ""

    def ids(self) -> str:
        target = self.root / "ids.log"
        return target.read_text() if target.exists() else ""

    def run(self, *args, euid=None, timeout=60, stdin=None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        if euid is not None:
            env["NEMESIS_TEST_EUID"] = str(euid)
        return subprocess.run(
            [str(self.cli), *map(str, args)],
            cwd=str(self.workdir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            errors="replace",
            timeout=timeout,
            input=stdin,
        )

    def daemon(self, *args) -> subprocess.Popen:
        return subprocess.Popen(
            [str(self.cli), *args],
            cwd=str(self.workdir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def package(self, id_="my.tool", *, extra_meta="", sections="", files=None, log_hooks=True, name="my.zip") -> Path:
        """A package whose hooks append their name and cwd to <root>/hooks."""
        members = dict(files or {})
        hooks = ""
        if log_hooks:
            for key, rel in [("preinstall", "pre"), ("postinstall", "post"), ("preuninstall", "preun"), ("postuninstall", "postun")]:
                hooks += f"{key}=scripts/{rel}.sh\n"
                members.setdefault(f"scripts/{rel}.sh", f'echo "{rel} $(pwd)" >> "{self.root}/hooks"\nexit 0\n')
        members["NEMESIS.data"] = f"[nemesis]\nversion=1\nid={id_}\nname=My Tool\ntypes=application\n{hooks}{extra_meta}\n{sections}"
        return make_zip(self.workdir / name, members)

    def hooks(self) -> list:
        target = self.root / "hooks"
        return target.read_text().splitlines() if target.exists() else []

    def db(self, name: str = "packages.db") -> str:
        target = self.root / "data" / "nemesis" / name
        return target.read_text() if target.exists() else ""

    def app(self, id_: str = "my.tool") -> Path:
        return self.root / "data" / "nemesis" / "apps" / id_

    def log(self) -> str:
        target = self.root / "data" / "nemesis" / "nemesis.log"
        return target.read_text(errors="replace") if target.exists() else ""

    def stick(self, descriptor: str, files: dict = None, mount: str = "storage/usb0", emergency: bool = None) -> Path:
        """A stick is a recovery stick by default when Android is down, as it must be to act."""
        usb = self.root / mount
        usb.mkdir(parents=True, exist_ok=True)
        for name, data in (files or {}).items():
            target = usb / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data if isinstance(data, bytes) else data.encode())
        (usb / "NEMESIS.data").write_text(descriptor)
        if emergency if emergency is not None else self.prop("sys.boot_completed") == "0":
            (usb / "NEMESIS.emergency").write_text("")
        # usb0_mounted() matches the literal unit path, so this names the mount as the unit does.
        (self.root / "proc_mounts").write_text(f"/dev/block/sda1 /{mount} vfat rw 0 0\n")
        return usb

    def unplug(self) -> None:
        (self.root / "proc_mounts").write_text("")

    def answer_dialogs(self, reply: str) -> None:
        """Manager stand-in: am records the dialog and the reply follows, as a tap would.

        It must come after am returns, because wait_reply clears any older reply first (usb.c:270).
        """
        session = self.root / "data" / "nemesis" / "usb-session"
        executable(self.bin("am"), f'echo "am $*" >> "{self.root}/calls"\n(sleep 1; mkdir -p "{session}"; echo {reply} > "{session}/reply") >/dev/null 2>&1 &\n')

    def session_log(self) -> str:
        target = self.root / "data" / "nemesis" / "logs" / "usb-session.log"
        return target.read_text() if target.exists() else ""

    def kill_all(self) -> None:
        marker = str(self.root) + "/"
        for name in os.listdir("/proc"):
            if not name.isdigit() or int(name) == os.getpid():
                continue
            try:
                cmdline = Path(f"/proc/{name}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            except OSError:
                continue
            if marker in cmdline:
                try:
                    os.kill(int(name), signal.SIGTERM)
                except OSError:
                    pass
        time.sleep(0.1)


def wait_for(check, timeout: float = 15.0, step: float = 0.1) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return True
        time.sleep(step)
    return bool(check())


def session_unit() -> FakeUnit:
    keep = os.environ.get("NEMESIS_NATIVE_BUILD")
    out_dir = Path(keep) if keep else Path(tempfile.mkdtemp(prefix="nem-build-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    # Short on purpose: sockets live under this root and sun_path holds 108 bytes.
    root = Path(tempfile.mkdtemp(prefix="nem-", dir=os.environ.get("NEMESIS_UNIT_TMP", "/tmp")))
    return FakeUnit(root, build(root, out_dir))


def zip_bytes(members: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buffer.getvalue()
