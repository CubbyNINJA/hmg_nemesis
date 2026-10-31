"""Runs real package hooks against a fake unit under a temp directory.

/system and /data in a copy of the scripts point at the temp tree, and PATH holds
stubs for the unit's tools. Nothing outside the temp directory is touched.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SH = os.environ.get("NEMESIS_TEST_SH") or shutil.which("mksh") or shutil.which("sh")

STUBS = {
    "getprop": 'cat "{root}/props/$1" 2>/dev/null\nexit 0\n',
    "setprop": 'printf "%s\\n" "$2" > "{root}/props/$1"\necho "setprop $1=$2" >> "{root}/calls"\n',
    "settings": (
        'case "$1" in\n'
        'get) cat "{root}/wifi_on" 2>/dev/null || echo null ;;\n'
        'put) printf "%s\\n" "$4" > "{root}/wifi_on" ;;\n'
        'delete) rm -f "{root}/wifi_on" ;;\n'
        "esac\n"
    ),
    "am": 'echo "am $*" >> "{root}/calls"\n',
    "sync": "exit 0\n",
    "chown": "exit 0\n",
    "mount": "exit 1\n",
}


def sh_path(path: Path) -> str:
    """A path sh accepts. Git Bash needs /c/x, since C:/x breaks PATH on the colon."""
    text = Path(path).as_posix()
    if sys.platform == "win32" and text[1:2] == ":":
        return "/" + text[0].lower() + text[2:]
    return text


def write_lf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def stub(path: Path, body: str) -> None:
    write_lf(path, "#!/bin/sh\n" + body)
    path.chmod(0o755)


class Unit:
    def __init__(self, tmp: Path, pkg_source: Path = None):
        self.tmp = Path(tmp)
        self.root = sh_path(self.tmp)
        (self.tmp / "props").mkdir(parents=True, exist_ok=True)
        for name, body in STUBS.items():
            stub(self.tmp / "stub" / name, body.format(root=self.root))
        self.set_remount(True)
        stub(self.tmp / "system" / "bin" / "reboot", f'echo reboot >> "{self.root}/calls"\n')
        self.pkg = self.tmp / "pkg"
        if pkg_source:
            self._copy_package(pkg_source)

    def set_remount(self, works: bool) -> None:
        body = f'echo "nemesis $*" >> "{self.root}/calls"\nexit {0 if works else 1}\n'
        stub(self.tmp / "system" / "xbin" / "nemesis", body)

    def set_mount(self, works_for: str) -> None:
        """Toolbox mount succeeds only for these exact arguments."""
        stub(self.tmp / "stub" / "mount", f'case "$*" in\n"{works_for}") exit 0 ;;\nesac\nexit 1\n')

    def break_tool(self, name: str) -> None:
        """The tool always fails, as setprop or settings do with a wedged framework."""
        stub(self.tmp / "stub" / name, "exit 1\n")

    def enable_faults(self) -> None:
        """Puts failing wrappers for cp, rm and mkdir first on PATH. See fail, fail_empty and plant."""
        faults = f"{self.root}/faults"
        (self.tmp / "faults").mkdir(exist_ok=True)
        check = (
            'list="{faults}/{tool}"\n'
            'if [ -f "$list" ]; then\n'
            '  for a in "$@"; do\n'
            "    while read -r p skip once; do\n"
            '      case "$a" in *"$p"*)\n'
            '        c=$(/usr/bin/cat "$list.count" 2>/dev/null || echo 0)\n'
            "        c=$((c + 1))\n"
            '        echo "$c" > "$list.count"\n'
            '        if [ -n "$once" ]; then\n'
            '          [ "$c" -eq "$((${{skip:-0}} + 1))" ] && exit 1\n'
            "        else\n"
            '          [ "$c" -gt "${{skip:-0}}" ] && exit 1\n'
            "        fi\n"
            "        ;;\n"
            "      esac\n"
            '    done < "$list"\n'
            "  done\n"
            "fi\n"
        )
        empty = (
            'if [ -f "{faults}/cp.empty" ]; then\n'
            '  for last in "$@"; do :; done\n'
            '  read -r p < "{faults}/cp.empty"\n'
            '  case "$last" in *"$p"*) : > "$last"; exit 0 ;; esac\n'
            "fi\n"
        )
        plant = (
            'PATH=/usr/bin:/bin mkdir "$@" || exit 1\n'
            'if [ -f "{faults}/plant" ]; then\n'
            "  while read -r kind p; do\n"
            '    if [ -d "${{p%/*}}" ] && [ ! -e "$p" ]; then\n'
            '      case "$kind" in\n'
            '        full) PATH=/usr/bin:/bin ln -s /dev/full "$p" ;;\n'
            '        dir) PATH=/usr/bin:/bin mkdir "$p" ;;\n'
            "      esac\n"
            "    fi\n"
            '  done < "{faults}/plant"\n'
            "fi\n"
        )
        # /dev/full reads as endless zeros, so a planted temp file must never reach the real cp.
        # Code that ignored the failed write commits it, so that shows up as a corrupt file and success.
        device = (
            'for last in "$@"; do :; done\n'
            'for a in "$@"; do\n'
            '  if [ -c "$a" ]; then echo CORRUPT > "$last"; exit 0; fi\n'
            "done\n"
        )
        stub(self.tmp / "stub" / "cp", check.format(faults=faults, tool="cp") + empty.format(faults=faults) + device + 'PATH=/usr/bin:/bin exec cp "$@"\n')
        stub(self.tmp / "stub" / "rm", check.format(faults=faults, tool="rm") + 'PATH=/usr/bin:/bin exec rm "$@"\n')
        stub(self.tmp / "stub" / "mkdir", check.format(faults=faults, tool="mkdir") + plant.format(faults=faults))

    def fail(self, tool: str, pattern: str, skip: int = 0, once: bool = False) -> None:
        """The tool fails once an argument has matched pattern more than skip times.

        With once, only that one call fails, so a later step cannot hide an ignored failure.
        """
        with open(self.tmp / "faults" / tool, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{pattern} {skip} {'once' if once else ''}\n")

    def fail_empty(self, pattern: str) -> None:
        """cp reports success but leaves an empty destination, as a full partition can."""
        write_lf(self.tmp / "faults" / "cp.empty", pattern + "\n")

    def plant(self, kind: str, rel: str) -> None:
        """A path that cannot be written. `full` opens but fails on write; `dir` fails on open.

        It appears at the next mkdir once its parent exists, so a wiped backup directory still gets it.
        """
        with open(self.tmp / "faults" / "plant", "a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{kind} {self.root}{rel}\n")

    def _rewrite(self, text: str) -> str:
        text = text.replace("\r\n", "\n")
        text = re.sub(r"^export PATH=.*$", 'export PATH="$NEMESIS_TEST_PATH"', text, flags=re.M)
        return re.sub(r"/(data|system)/", lambda m: f"{self.root}/{m.group(1)}/", text)

    def _copy_package(self, source: Path) -> None:
        for item in Path(source).rglob("*"):
            if not item.is_file():
                continue
            dest = self.pkg / item.relative_to(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if item.suffix == ".sh":
                write_lf(dest, self._rewrite(item.read_text(encoding="utf-8")))
            else:
                dest.write_bytes(item.read_bytes().replace(b"\r\n", b"\n"))

    def run(self, *argv, cwd: Path = None) -> subprocess.CompletedProcess:
        env = dict(os.environ, NEMESIS_TEST_PATH=f"{self.root}/stub:/usr/bin:/bin", LC_ALL="C")
        return subprocess.run(
            [SH, *argv],
            cwd=str(cwd or self.pkg),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            timeout=120,
        )

    def hook(self, name: str) -> subprocess.CompletedProcess:
        return self.run(f"scripts/{name}.sh")

    def functions(self, body: str) -> subprocess.CompletedProcess:
        """Runs body after sourcing the package's common.sh."""
        script = self.tmp / "driver.sh"
        write_lf(script, f'. ./scripts/common.sh\nexport PATH="$NEMESIS_TEST_PATH"\n{body}\n')
        return self.run(sh_path(script))

    def path(self, rel: str) -> Path:
        return self.tmp / rel.lstrip("/")

    def put(self, rel: str, data: bytes) -> Path:
        target = self.path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def read(self, rel: str) -> bytes:
        return self.path(rel).read_bytes()

    def exists(self, rel: str) -> bool:
        return self.path(rel).exists()

    def calls(self) -> str:
        target = self.tmp / "calls"
        return target.read_text(encoding="utf-8") if target.exists() else ""

    def snapshot(self, *roots) -> dict:
        out = {}
        for rel in roots:
            base = self.path(rel)
            for item in base.rglob("*"):
                if item.is_file():
                    out[item.relative_to(self.tmp).as_posix()] = item.read_bytes()
        return out
