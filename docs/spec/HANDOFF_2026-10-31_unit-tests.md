# Handoff: retroactive unit tests

**CI/CD removed at the user's request on 2026-10-31.** The GitHub Actions workflow and the merge ruleset were deleted and are no longer goals. All tests run by hand.

Written 2026-10-31, before a reboot to enable virtualization. Nothing from this work is committed or pushed.

## Goal

Assess the repo, then build tests from the requirements in the docs and code. Then:

- run a full mutation and property loop once the suite is green
- keep the native code and the builder at 90% coverage or better, checked by hand

## Where it stands

| Item | State |
|---|---|
| Assessment | Done. Gaps are listed below. |
| WSL2 | Ubuntu 26.04 installed with all tools. LF working copy at `~/hmg_nemesis`, resynced from the Windows checkout, which stays the source of truth. |
| Full suite (WSL, `mksh`, C tests required) | **639 passed, 1 skipped, 40 expected failures.** The skip is the G22 scenario, which needs `NEMESIS_SANITIZE=1`. |
| Coverage | Native **91%** (2103 of 2299 lines), builder **100%**. Both clear the 90% gate. |
| Doc corrections D1, D2, D4, D5, D6 | Done, and pinned by `test_docs.py` |
| `AGENTS.md` section 10 (BDD contributing) | Done, and reviewed by a critic agent |
| Mutation loop | Shell and builder under `mksh`: **533 of 561 killed** at the last full run, before the final five scenarios. 1,036 C mutants are defined in `mutate.py` but **not yet run**. See below. |
| Property tests | `test_native_properties.py` passes, on the real CLI. Under ASan and UBSan the only finding is G22; 300 damaged zips did not crash it. |
| `pkg_live_snapshot.sh` | Path fix plus `--rbind /usr/lib` for Ubuntu 26.04 and WSL. **Passes under `mksh` in WSL, all scenarios.** Not yet run on 24.04. |
| `setup_press_test.py` | **Passes in WSL.** |

## Upstream sync: done

The 9 commits up to `f889df5` are in this working tree. What came with them:

- `5650dae` taught `wireless-projection/scripts/common.sh` to strip `\r` when matching
  `featurexml.cfg` lines and to keep the original bytes when writing.
- `Packages/Enable Wireless Features.zip` was rebuilt from that source, and `Packages/README.md`
  gained six community rows.
- `README.md` gained the website link, the incompatible-car table and the two FAQ sections.

Follow-ups from the sync:

- The CRLF scenario is added: `test_wireless_install_patches_a_crlf_feature_file_and_keeps_its_line_endings`
  and `test_wireless_uninstall_restores_a_crlf_feature_file_byte_for_byte` in
  `Sources/tests/unit/test_hooks.py`. The first was confirmed red against the pre-sync `common.sh`
  and green against the synced one.
- **The mutation loop still has to be rerun.** `common.sh` line numbers shifted, so every `W nnn`
  entry in the mutation table below points at the wrong line now.

## Environment: resolved

WSL2 with Ubuntu is installed and running, so the full suite including the C tests runs locally.
It is a plain copy at `~/hmg_nemesis` with no `.git`; the Windows checkout at
`/mnt/c/Users/Gaming/hmg_nemesis` stays the source of truth. Resync before a run:

```sh
wsl.exe -d Ubuntu -- bash -lc 'rsync -a --delete --exclude .git --exclude __pycache__ /mnt/c/Users/Gaming/hmg_nemesis/ ~/hmg_nemesis/'
```

The Windows box has `core.autocrlf=true` in the system gitconfig, so this repo carries a local
`core.autocrlf=input`. That keeps blobs LF but does not protect any other clone; G1 is still open.

## Next steps, in order

Steps 1 to 5 are done. Harness fixes found on the first compile: mounts are written as the unit names them, because `usb0_mounted` matches literal paths; the `am` stand-in replies after a delay, because `wait_reply` clears older replies first.

6. **Close the mutation gaps:**
   - Add a fault-injecting stub for `cp` and for writes.
   - Add a test where the `nemesis` remount fails and `mount` succeeds.
7. **Rerun the mutation loop** under `mksh` with `NEMESIS_TEST_SH`, and add C mutants to `mutate.py`. Use `--jobs 3` or lower. Loop until only justified-equivalent mutants survive, and list those for the user.
8. **Add property tests against the real C parsers:** INI, zip, id and path rules, DB round trips. Hypothesis is fine inside WSL. The current ones use seeded stdlib `random`.
9. **Ask before** committing or pushing.

## Decisions made by the user

- Toolchain: **WSL2**.
- Known bugs: **strict expected failures** tagged with a G number (`known_bug` in `helpers.py`). Fixes are separate specs.
- D1 to D6: all recommendations accepted (see the table below).
- Patch `pkg_live_snapshot.sh` so it runs under WSL.
- No PII step. The user was misremembering that a script existed.

## Defaults I chose, not yet confirmed

- The 90% applies to `make_nemesis_package.py` and `Sources/native/*.c`, each separately. Not counted: shell hooks (mutation testing checks them instead), Manager Java (needs Android), and `make_troubleshooter_tar.py` (installer, out of scope per `AGENTS.md`).
- New test files live in `Sources/tests/unit/`, with no production code changes.

## Tests that cannot run without extra files

- `emu_nemesis_flow.py` needs a QEMU image at `/home/xwtk/EV6_260507/emulator`, which isn't shipped.
- `pkg_carplay_session.sh` needs stock firmware files and a package that is not in this checkout.

## Mutation results so far

Baseline under `mksh` was 234 of 561 killed. The last full run of shell and builder mutants, on the synced `common.sh`, was **533 of 561 killed, 28 survived**. Five scenarios added after it target five of those; a rerun has to confirm that. Lines are in `adb-tcp` (A) or `wireless-projection` (W).

| Group | Count | Lines | State |
|---|---|---|---|
| Failed `: > "$_tmp"` | 14 | A 140, 187; W 166, 213, 400, 432, 459 (2 each) | Equivalent on this `mksh` (run): the failed redirect aborts the function with status 1, so `\|\| return` never runs. The outer flow still goes read-only and keeps the snapshot. Not checked on the unit's older `mksh`. |
| One failed write, later writes work | 5 | A 150, 197; W 176, 223, 440 | Scenarios added, rerun to confirm. |
| Dropped-lines guard | 4 | A 161, 162; W 187, 188 | Likely equivalent: every input line gives an output line, so it cannot trigger. |
| `mkdir` failure | 3 | A 270; W 481, 511 | Likely equivalent: the next `cp` fails with the same result. |
| After `exec reboot` | 2 | `post.sh` 20, both packages | Likely equivalent; a scenario with no reboot binary is added, and the rerun will show it. |

**Next:** run the C mutants per file, smallest first, with `--only Sources/native/<name>.c` and `NEMESIS_REQUIRE_NATIVE=1`. They may take hours.

`/dev/full` reads as endless zeros, so a planted temp file must never reach the real `cp`. One mutant filled the 7.8 GB `/tmp` that way. The fault `cp` stub now guards against it, and `mutate.py` caps files at 256 MB.

## Code gaps

Traced by reading the source and checked by a critic agent, except where marked as run. Fixes are not done.

| # | Where | Problem | Fix |
|---|---|---|---|
| G1 | repo root | With `autocrlf=true` and no `.gitattributes`, packages built on Windows ship CRLF scripts (run) | `.gitattributes` with `*.sh text eol=lf` and `NEMESIS.* text eol=lf`; builder strips `\r` or fails |
| G2 | `pkg.c:651` | A failed `--update` deletes the old tree, but the DB still lists the package | Move the old tree aside until the new install succeeds |
| G3 | `pkg.c:95` | `name=x\|1\|y` makes a package read as locked, against `SCOPE.md` | Reject `\|` in `name` and service fields |
| G4 | `pkg.c:477` | A failed `preuninstall` is ignored and the tree is deleted | Abort on non-zero, except during wipe |
| G5 | `pkg.c:313,322` | `sys.boot_completed=0` with no `pm` timeout. A concurrent USB insert skips its dialog, and a killed CLI leaves it at 0 | `pm` timeout, plus a lock file checked by `framework_up` |
| G6 | `svc.c:114` | `.fails` is never cleared, so a reinstalled package stays disabled | Unlink in `svc_remove_prefix` and on manual start |
| G7 | `svc.c:98,266` | `service stop` on a `restart` service is undone within about 2s | `.stopped` marker that svcmgr honours |
| G8 | `usb.c:11,91` | `root -c` swallows the rest of a compound command mentioning remount; ro/rw found by substring | Intercept only a plain `mount` command with no `;&\|` |
| G9 | `pkg.c:526` | Non-zero `postinstall`: resolved as D6, documented behaviour | None |
| G10 | `pkg.c:930,940` | Wipe kills no daemons, and `log_msg` recreates `/data/nemesis` | Stop daemons first; log before `rm_rf` |
| G11 | `svc.c:124,265` | A deferred `instance` service in default mode never starts | Deferred flag, started by the svcmgr loop |
| G12 | `pkg.c:476-479` | svcmgr can restart a service between stop and deregister | Deregister first |
| G13 | `pkg.c:92,185` | `pkg-list` rewrites the DBs unlocked; rows can be lost | `pidfile_lock`, temp file plus rename |
| G14 | `zip.c:31,177` | No CRC check; a short member writes heap bytes to disk | Check `total_out` and the CRC |
| G15 | `zip.c:232` | Nested `x/NEMESIS.data` can supply metadata; manifest truncates at 8191 bytes | Prefer root; fail on truncation |
| G16 | `ini.c:35`, `pkg.c:539` | BOM rejected; text past 1023 chars parses as a new line; lowercase manifest loses hooks on uninstall | Strip BOM; discard overflow; normalise the name |
| G17 | `util.c:84` | Mount wrapper misses `-o rw,remount /system` (run) | Add `*',remount'*` to the outer case |
| G18 | `make_nemesis_package.py` | Accepts a bad id or missing path; zips its own output (run) | Validate; skip the output file |
| G19 | `su.c:51,58` | A 768-byte argument passes the client and fails on the server | Client checks `n >= SU_MAX_ARG` |
| G20 | `adb-tcp/scripts/common.sh` | Restore isn't byte-exact with duplicate keys or no trailing newline, though a full copy is kept (run) | Restore from the full snapshot |
| G21 | `pkg.c:412-413` | The package root is prepended to a `kind=instance` `path`, so `am` gets a file path instead of the documented component (run). Needs a maintainer ruling, as docs and code disagree | Skip the prefix when `kind=instance` |

| G22 | `usb.c:563,566` | The handler frees the descriptor, then logs `type` and `path` from it: a use-after-free in the root daemon on every `execute` or `install` insertion (run, found by ASan). Without ASan the log usually looks right | Call `ini_free` after `log_handled`, as the sibling branches do |

New gaps start at **G23**.

## Doc and code rulings

| # | Topic | Ruling | State |
|---|---|---|---|
| D1 | Zip member with `..` | Rejects the whole package | Docs fixed |
| D2 | USB with the id already installed | Android up: uninstall first. Android down: updates | Docs fixed |
| D3 | `uid=system` on `[app.N]` | Docs are right, code must honour it | Expected failure, code not fixed |
| D4 | Paths in `NEMESIS.data` | Reject `..`, keep absolute | Docs fixed; `..` check is an expected failure |
| D5 | Unplug during a running script | Doesn't end the wait | Docs fixed |
| D6 | Non-zero `postinstall` | Returns failure, package stays installed | Docs fixed |

## Scope conflicts still open for the user

- `SCOPE.md` and doc 02 say no OEM app is patched, but `projection.tbt` installs dex-injected stock APKs.
- `docs/drive-mode-memory-package.md` explores changing a vehicle drive mode, including input injection, while `SCOPE.md` bars powertrain actuation. It now opens with a scope gate that stops Strategy A short of implementation, but the ruling itself is still the owner's. Only its first 60 lines were read, and no tests were written for it.

## Not reviewed

Most of the Manager Java, `usb_bundle/` scripts, `install-recovery.sh`, the rest of `emu_nemesis_flow.py`, and the projection hook scripts. `projection-tbt` has no behaviour tests.

## Working tree

Added by this work:

- `Sources/tests/unit/`
- this file

Edited by this work:

- `Sources/tests/pkg_live_snapshot.sh`
- `04-Packages.md`
- `05-Run-Scripts.md`
- `docs/creating-a-package.md`
- `AGENTS.md`

**Also in the working tree, from the owner's own reframing of this checkout:** the deletions of
`01-DSP-OTA-troubleShooter.md` and `03-Install-and-Uninstall.md`, the changes to `02`,
`README.md` and `emu_nemesis_flow.py`, the untracked `SCOPE.md` and the other files in `docs/`.
`AGENTS.md`, `04` and `05` carry both sets of changes and were not split apart.

Sanitization pass applied for hand-off, on top of the owner's reframing:

- Test fixtures no longer use hostile names: `tmp-evil`/`pwned` became `tmp-badid`/
  `traversal-probe` in `emu_nemesis_flow.py`, `../../evil` became `../../traversal`,
  `evil.zip` became `pipe-name.zip`, `../../evil.sh` became `../../outside.sh`, and the injected
  `id=evil` became `id=injected`. The `AGENTS.md` section 10 example was updated to match.
- `docs/drive-mode-memory-package.md` opens with a scope gate: Strategy A commands a drive-mode
  change, which `SCOPE.md` bars as powertrain actuation, so it needs a maintainer ruling before
  anyone builds it. Phase 1 and Phase 2B are unaffected.
- The incoming `README.md` wording was merged into this checkout's framing: the incompatible-car
  table describes the DSP/OTA path as the delivery mechanism rather than as a flaw, and the
  `Quick Installation Guidance` heading the local rewrite had dropped is back.
- A `.gitignore` covers `__pycache__/`, coverage files and the build outputs, so the untracked
  list is just real work. `python -B` is still fine to use.

This work is on branch `sync/upstream-crlf-and-docs` of the fork `CubbyNINJA/hmg_nemesis`, on top of
upstream `f889df5`. The fork's `main` was left as it was, at `d6031bd`, which is behind upstream.

## Key commands

```sh
python -B -m pytest Sources/tests/unit -q                       # host suite
NEMESIS_REQUIRE_NATIVE=1 python3 -m pytest Sources/tests/unit   # WSL, C tests can't skip
NEMESIS_TEST_SH=/usr/bin/mksh python3 -m pytest Sources/tests/unit
python -B Sources/tests/unit/mutate.py --jobs 3 --json out.json
```
