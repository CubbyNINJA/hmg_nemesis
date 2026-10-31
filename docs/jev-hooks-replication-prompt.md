# Prompt: replicate the Jev hook system in a project

> Paste everything below the line into a Claude Code session opened at the root of the target
> project. It builds the same Jev-backed hook system used in `hmg_nemesis`: one standard-library
> Python package of Claude Code hooks. Deterministic rules come first. TypeSafe **Jev** judgments are
> used where semantic understanding helps. Every gate fails open.

---

You are setting up **Jev hooks** for this project: Claude Code hooks (configured in
`.claude/settings.json`) that enforce project rules, save context, and steer the session. Follow
the architecture, contracts and lessons below exactly. Adapt only the parts marked **ADAPT**. Before
writing code, run the interview in §7 and get my sign-off on each decision.

## 1. Non-negotiable design rules

1. **Python 3.8+, standard library only.** No venv, no pip, no npm. Hooks must run in any shell
   via `py -3` (Windows) or `python3`. Use `from __future__ import annotations` so `dict | None`
   hints work on 3.8.
2. **Fail open, always.** Any exception, timeout, missing API key or malformed answer means *no
   decision*, so the tool call proceeds. `main()` wraps everything in `try/except Exception`, audits
   the error, and returns 0. The one exception is a rule-only deny (forbidden file, critical file),
   which needs no network.
3. **Rules before Jev.** Known lists, globs, regexes, sizes and git state stay in code. Ask Jev only
   for judgments code can't make (verbosity, a chunk's main responsibility, how risky a diff is).
4. **Send the minimum.** Never send forbidden docs, binary payloads or critical files to Jev. Cap
   every state field (chars). Keep code bodies out of the audit log (`log_state=False`).
5. **Cache by file version.** Key Jev results by `(path, size, mtime_ns)` (files) or a SHA-1 of the
   change text (diffs), so a repeat event costs no call.
6. **Everything tunable lives in `config.json`**: thresholds, globs, path tiers, header text, role
   lists.
7. **One dispatcher.** `hooks.py <gate>` selects a handler from `HANDLERS`. Settings entries differ
   only in the gate argument and timeout.

## 2. Layout

```
.claude/
  settings.json                 # hook registrations
  hooks/<project>/
    gatelib.py                  # Jev client, question builders, audit, Session, output helpers, paths, git
    hooks.py                    # dispatcher + gates
    config.json                 # all tunables
    README.md                   # gate table, Jev wiring, data-egress statement, how to disable
    logs/                       # audit.jsonl, state/<session>.json, cache/  (gitignore this)
```

A **global** variant (for example a `PreCompact` gate in `~/.claude/hooks/jev-compact/`) is the
same pattern collapsed into one self-contained script with its own `config.json` deep-merged over
built-in defaults.

## 3. `gatelib.py` contract

```python
HERE = Path(__file__).resolve().parent
LOG_DIR = Path(os.environ.get("JEV_GATES_LOG_DIR") or HERE / "logs")   # override for tests
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
ANSWER_ERRORS = (KeyError, TypeError, ValueError)
class JevError(Exception): ...

def ask(state, questions, timeout=None) -> dict
    # POST {TYPESAFE_BASE_URL or https://api.typesafe.ai}/v1/systemone
    # headers: Authorization: Bearer $TYPESAFE_API_KEY, Content-Type: application/json
    # body: {"model": $TYPESAFE_DEFAULT_MODEL or CONFIG["model"] ("jev-latest"), "state": ..., "questions": {...}}
    # raise JevError on: no key, HTTPError, URLError/TimeoutError/OSError, response without "answers" dict
def noul(instructions, criteria=None)  -> {"type": "noul", ...}
def choice(instructions, criteria: dict) -> {"type": "choice", "instructions": ..., "criteria": {name: description}}
def score(instructions, levels: list)  -> {"type": "score", "instructions": ..., "criteria": [level0, level1, ...]}  # 2-10 levels, lowest first
def p_yes(answers, qid)   -> float(answers[qid]["noul"])
def picked(answers, qid)  -> (answers[qid]["choice"], answers[qid]["probabilities"][choice])
def score_of(answers, qid) -> float(answers[qid]["score"])            # probability-weighted level
def call(gate, state, questions, ask_fn=None, timeout=None, log_state=True) -> dict | None
    # resolves `ask` at call time (so tests can monkeypatch gatelib.ask), audits answers + latency, None on JevError
def audit(entry)            # append JSON line + ts to LOG_DIR/audit.jsonl; swallow OSError
class Session(session_id)   # LOG_DIR/state/<id>.json; .data dict; .save() writes tmp + os.replace (hooks run in parallel)
def deny(reason)            -> {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}
def context(notes, event="PreToolUse") -> {"hookSpecificOutput": {"hookEventName": event, "additionalContext": "\n".join(notes)}}
def project_root(cwd)       # $CLAUDE_PROJECT_DIR, else cwd
def to_native(p, cwd)       # Git Bash /c/x -> C:/x, expand ~ and env vars, resolve relative to cwd
def rel(path, root)         # project-relative POSIX path, or str(path) if outside
def matches_any(path, root, globs)  # fnmatch, case-insensitive, against rel path or basename
def git(cwd, *args)         # subprocess, 4 s timeout, None on failure, decode utf-8 "replace"
```

Jev answer shapes (verified against docs.typesafe.ai):
- choice: `{"type":"choice","choice":"name","confidence":0..1,"probabilities":{"name":p,...}}`
- score: `{"type":"score","score":1.43,"confidence":0..1,"legend":{"0":"..."},"probabilities":{"0":p,...}}`
- noul: `{"noul": p_yes}`

Questions in one request share one `state` and are evaluated in parallel. Question IDs are not
sent to the model, so put the full meaning in `instructions` and reference state fields with
backticks (`` `code` ``, `` `diff` ``).

## 4. Hook I/O contract (Claude Code)

- Input: one JSON object on stdin with `session_id`, `transcript_path`, `cwd`,
  `hook_event_name`, plus event fields (`tool_name`, `tool_input` for PreToolUse; `source` for
  SessionStart; `prompt` for UserPromptSubmit; `agent_id` when the event comes from a subagent).
- Output: nothing (allow), or one JSON object on stdout (`deny(...)` / `context(...)`). Exit 0.
- **A hook cannot rewrite what a built-in tool returns, or the user's prompt.** To save tokens on a
  Read, *deny* the full read and carry the smaller view in `permissionDecisionReason`. Let ranged
  reads (`offset`/`limit`) pass, so exact text is always one call away.
- Hooks with the same matcher run **in parallel**. Any deny wins. A new handler on `Read` must
  itself skip files another gate already denies.
- `$CLAUDE_PROJECT_DIR` works in the command string:
  `py -3 "$CLAUDE_PROJECT_DIR/.claude/hooks/<project>/hooks.py" <gate>`.

## 5. The gates

### Baseline rule gates (ADAPT lists to the project)
| Gate | Event | Effect |
|---|---|---|
| Forbidden content | PreToolUse `Read\|Bash\|PowerShell\|Grep` | deny Read/Grep, and shell readers (cat/sed/head/grep/Get-Content…, parsed per pipeline stage, following `cd`), of `forbidden_docs` and `binary_payloads` |
| Git safety | same | deny commit/push on `protected_branches`; deny add/commit of forbidden/payload files |
| CRLF | Bash | deny `sed -i` without `-b` on a CRLF file |
| Heredoc | Bash | deny a Python heredoc whose body contains `\\` (the harness collapses it) |
| Lint | PreToolUse `Edit\|Write` | project format rules: deny invalid manifest values; advisory notes for style |
| Critical files | `Edit\|Write` | deny edits to `critical_globs` (signing, boot, entry points) |

### Jev gates
| Gate | Event | Jev primitive | Effect |
|---|---|---|---|
| Edit-in-scope | `Edit\|Write` | noul: is this file part of the task in the recent prompts? | advisory once per file/session if p < 0.35 |
| Question gate | `AskUserQuestion` | noul over a decision register's shortlisted rows | deny once if already settled (p ≥ 0.8) |
| Diff risk CLI | manual | noul over the first 6 KB of `git diff` | prints low/elevated/critical |
| **D9 Normalize** | PreToolUse `Read` | **choice** compact/typical/verbose on the first 4 KB | deny with NORMALIZED view |
| **D10 Topology map** | PreToolUse `Read` | **choice** role + **score** complexity, one request per chunk, in parallel | deny with JSON map |
| **D11 Risk steering** | `SessionStart`, `UserPromptSubmit` | **score** Low/Medium/High over the change | inject the tier header |

#### D10: topology map (files ≥ `min_lines` 600 or ≥ `min_bytes` 24 KB, in `scope_prefixes`)
1. Skip unless `tool_input` has no `offset`/`limit`, the path is in scope, the extension isn't in
   `skip_exts`, the file isn't forbidden/payload, and the first 8 KB has no NUL byte.
2. **Chunk**: candidate starts are non-blank lines at brace depth ≤ base (0, or 1 for Java/Kotlin)
   that follow a blank line or a line starting with `}`. Count brace depth after stripping string
   literals and `//` / whitespace-`#` comments. Merge spans up to `target_chunk_lines` (80, raised
   to `ceil(n / max_chunks)`). Hard-split spans over 2× target.
3. Per chunk, rule facts: `lines` "a-b" (1-based, inclusive), up to 6 `symbols` (def/class, shell
   `name()`, C column-0 definitions minus keywords, Java methods/classes), `branches` count.
4. **Critical files → rules-only map, no Jev.** Otherwise run one request per chunk (state =
   `{file, lines, symbols, code[:3000]}`, so cost stays linear in file size) via
   `ThreadPoolExecutor(workers=8)`, with a 25 s overall deadline and hook timeout 40 s. Questions:
   `role` = choice over `config.fraction_gate.roles` (ADAPT; include an `other` no-match option);
   `complexity` = score over 4 levels (simple / moderate / involved / dense low-level). If **all**
   calls fail, return None (fail open).
5. Cache the map. Deny with: one-line explanation, how to follow up with `offset`/`limit`, a header
   JSON `{file, lines, bytes, roles: {role: count}, hotspots: [top-3 by complexity]}`, then one
   chunk JSON object per line.

#### D9: normalized view (`min_lines` 300 … `max_lines` 599, non-critical)
Dictionary, applied deterministically:
1. Drop banner/divider lines (`^\s*(#|//|;|--|\*)?\s*[-=#*~_+/\\]{4,}\s*$`, or one-line `/*----*/`)
   and empty comment lines. Never drop a line-1 shebang. Keep `/*` openers.
2. Strip trailing whitespace, collapse blank runs to one, rewrite leading indentation as one tab
   per level (unit = smallest space indent, clamped to 2–8).
3. Fold runs of ≥ 3 lines that are identical (`line  <xN>`) or similar (same first word and token
   count, or same shape after masking strings and numbers): keep the first and last, and put
   `<+K similar lines, La-Lb>` between them.
4. Alias up to 9 long repeated prefixes (path dirs `/a/b/` or dotted `a.b.`, length ≥ 12,
   count ≥ 3), choosing greedily by `(len-2)*count` and recounting after each replacement, as `§N`
   with a legend. Skip aliasing if `§` already occurs in the file.
5. Prefix each kept line with its original line number `N:`.
Gate: compute the saving against the Read-shaped size (`"%6d\t" + line`). Pass if it's under 15%.
Otherwise ask Jev (choice compact/typical/verbose on the first 4 KB) and cache `verbose_p`. Deny
only if p ≥ 0.5, with a banner saying the view is **NOT byte-exact** and that exact text needs
`offset`/`limit`. Record `(path, mtime)` in the Session so a **repeat full Read passes**.

#### D11: risk steering
1. Changed files = `git -c core.quotepath=off diff --name-only --no-renames HEAD` plus
   `git ls-files --others --exclude-standard`. `--no-renames` matters: a `git mv` of a critical or
   forbidden file must still show the old path. Drop the log dir (both `LOG_DIR` and `HERE/logs`),
   `__pycache__`, and forbidden docs if `ignore_forbidden_docs`.
2. Path tier per file: `critical_globs` → high, then `path_tiers.high/medium/low` (prefixes ending
   `/`, exact paths, or root-only globs such as `*.md`), else `default_tier`. Workspace path tier =
   max.
3. If below high: build the change text from
   `git --literal-pathspecs diff --no-renames HEAD -- <sendable tracked files>`. **Only run it when
   that list is non-empty.** An empty pathspec diffs the whole tree, including deleted forbidden
   docs. Add the first 1,200 bytes of each sendable untracked text file (read with
   `open().read(n)`, not the whole file), and cap the total at 6 KB. Ask a 3-level **score**
   (Low: docs/tests/tooling; Medium: runs on the device but no boot/partition/signing/privilege;
   High: boot, partitions, signing, privilege, native binaries, entry points). Cache by SHA-1 of the
   change. Jev tier: ≥ 1.5 high, ≥ 0.5 medium. **Jev only raises the tier.**
4. SessionStart: always inject `[Risk steering D11]` + `headers[tier]` + "(Driven by paths (tier):
   …; Jev change score x of 2.)". UserPromptSubmit: inject only when the tier differs from the
   Session's last tier. Skip subagent events (`agent_id`).

## 6. Lessons already learned (don't repeat these)
- **An empty git pathspec is "everything".** `git diff HEAD -- <filtered list>` with an empty list
  sent the deleted text of two forbidden docs to Jev in the original build. Guard every filtered
  git call with `if paths:`, and add a test that captures the `state` sent to Jev and asserts no
  forbidden diff headers (`a/<doc>`, `deleted file mode`) appear in it. Audit any other gate that
  diffs the tree (for example a manual risk CLI) for the same hole.
- Split file text on `\n` only, as Read does. `str.splitlines()` also breaks on `\f`, `\v`,
  `\x1c`–`\x1e`, ` ` and ` `, which shifts every later line number.
- Avoid overlapping quantifiers in symbol regexes. `(?:[\w*]+[\s*]+)+` backtracks exponentially
  on a `/*****…` line. Use `(?:\w+[\s*]+)+`.
- In brace-less languages (.py, .sh), chunk starts must be at column 0, or functions get cut at
  internal blank lines.
- Indent unit = GCD of space indents ≥ 2. A plain `min` is fooled by the 1-space ` * doc` lines in
  C block comments.
- Replace aliases only where the prefix starts a token (`(?<![\w.\-/])`). Keep `alias_max` ≤ 9 so
  `§1` followed by a digit stays unambiguous.
- Don't cache a D10 map with timed-out chunks, or they're never retried. When Jev blips, reuse the
  last D11 score instead of letting the tier flap.
- `gatelib.call` must catch `Exception`, not just `JevError`. A non-JSON 200 or `IncompleteRead`
  would otherwise sink a whole parallel batch.
- A lint gate keyed on `"<project>" in path` will lint the hook package itself. Its `path=` regex
  then denies ordinary Python like `path = ...`. Scope lint gates by file name/extension, not by
  substring.
- On Windows, a Git Bash `mktemp -d` path (`/tmp/...`) can't be opened by native Python. Use
  `cygpath -w`, or `tempfile.mkdtemp()` inside Python.
- `git ... | strip()` eats the leading space of the first `git status --porcelain` line. Prefer
  `diff --name-only` / `ls-files`.
- One request holding N chunks in a shared `state` makes every question pay for the whole state. Use
  one request per chunk for linear cost.
- Hook output is JSON-encoded with `ensure_ascii`, so non-ASCII such as `§` is safe.
- The console may show `�` for it during manual tests. That's display only.

## 7. Interview me before coding (one question at a time)
1. Language/location: stdlib Python in one `.claude/hooks/<project>/` package (recommended), or a
   standalone global script?
2. Which files are forbidden to read, which are binary payloads, which are critical (never
   auto-edited, never sent to Jev)?
3. Protected branches.
4. Do D9 and D10 stay separate, or merge (map only)? Size bands (default D9 300–599, D10 ≥ 600 lines
   / 24 KB) and `scope_prefixes`.
5. Data egress: may whole chunks of in-scope files be sent to `api.typesafe.ai`? (Default: yes,
   except critical files.)
6. Normalization rules to enable (whitespace, banners, aliases, folding), and any listing formats
   (smali, jadx, objdump, Ghidra) that need extra rules.
7. Path → tier map for D11, and the header text for each tier.
8. Chunk role list for D10 (keep `other`).

## 8. Definition of done (verify in this order)
1. `gatelib` imports and `config.json` parses after each edit (`py -3 -c "import gatelib"`).
2. An offline test script (in a scratch dir, `JEV_GATES_LOG_DIR` set to a temp dir, `gatelib.ask`
   monkeypatched) checks: D10 maps a large file, the map is under 1/3 the file size, the second read
   hits the cache, ranged reads pass, out-of-scope passes, a critical file gets a rules-only map with
   0 Jev calls, D9 denies a verbose mid-size file, a repeat full read passes, no key → D9/D10 pass,
   D11 SessionStart injects, an unchanged prompt is silent, a tier change injects, no key → path
   tier only. Regression checks:
   - no forbidden-doc diff in the captured D11 state, and no full-tree diff when no tracked file is
     sendable
   - the symbol regex is linear on a 40-`*` line
   - a form feed doesn't shift line counts
   - .py chunks start at column 0
   - the indent unit ignores 1-space doc lines
   - aliases are never applied inside a longer token
   - a partial D10 map isn't cached
   - a non-JevError in `call` returns None
3. One live call per Jev gate through `hooks.py` stdin. The audit log has no `error` entries and no
   code bodies.
4. `settings.json` is valid JSON. A real full Read of a large file in the session is replaced by
   the map.
5. A critic agent with fresh context reviews the code. Fix confirmed defects.
6. The README gate table, Jev wiring and data-egress statement are updated.
