# Profiling harness

A self-contained harness that runs this project's workloads ("packages") under
a set of profilers, writes timestamped artifacts, and is meant to be driven
from CI.

Packages and profilers are **discovered from the filesystem**. Nothing is
hardcoded: adding either is "create a directory with two small files", and the
listings, the installer and the CI matrix pick it up with no code change.

```
profiling/
  run_profiling.sh     entry point (a shim around lib/cli.py)
  install.sh           discovery-driven dependency installer
  config.env           global paths and defaults
  lib/                 the Python core + common.sh for bash hooks
  packages/<name>/     .env + package.sh   -- a workload
  profilers/<name>/    .env + profiler.sh  -- a way to measure it
  output/              run artifacts (gitignored)
  .state/              package init stamps (gitignored)
```

---

## Quick start

```bash
cd profiling
./install.sh                                   # install profiler dependencies
./install.sh --check                           # CI preflight: are the binaries there?

./run_profiling.sh --list-packages
./run_profiling.sh --list-profilers

./run_profiling.sh --package my-tool --profiler all
./run_profiling.sh --package my-tool --profiler all --dry-run   # resolve only
```

`packages/my-tool/` is a working example: a small, stdlib-only, offline
workload with its own `package_init`. It runs out of the box in a fresh
checkout, so you can exercise the harness before wiring up a real project.

---

## Commands

```
run_profiling.sh --package <sel> --profiler <sel> [--dry-run] [--force-init]
run_profiling.sh --list-packages  [--json]
run_profiling.sh --list-profilers [--json]
run_profiling.sh --remove-output[=all] [--package <sel>] [--profiler <sel>]
                                       [--keep N] [--dry-run]
```

`<sel>` is a single name, a comma-separated list, a repeated flag, or the
literal `all`:

```bash
--profiler time
--profiler time,py-spy
--profiler time --profiler py-spy
--profiler all
```

### `--profiler` is mandatory

Omitting it while `--package` is present is a usage error (exit 2). There is
no implicit default sweep — profiling every workload with every profiler is
expensive, so the harness makes you say what you want.

### `--list-packages` / `--list-profilers`

Terminal actions: print and exit. The default is a human-readable table; add
`--json` for a JSON array suitable for generating a CI matrix.

```bash
./run_profiling.sh --list-packages --json
```

### `--dry-run`

On a run: resolves everything — environment layering, `package_command`
overrides, the profiler's own argv — prints the exact command line each
profiler would execute plus the run directory it would use, and then executes
nothing and creates nothing.

```
[dry-run] my-tool / py-spy
    run dir : .../output/my-tool/py-spy/20260729-141230-a3f1
    kind    : python-module
    workdir : .../packages/my-tool
    workload: .../python -m my_tool.cli --input data/sample.json --iterations 5
    command : py-spy record --rate 100 --format flamegraph --subprocesses \
              --output .../profile.svg --pid <workload-pid>
```

The `command` line comes from the profiler's optional `profiler_dry_run` hook.
All four shipped profilers implement it by sharing one argv builder with
`profiler_wrap`, so what is printed cannot drift from what runs. A third-party
profiler without the hook falls back to naming the wrapper.

On `--remove-output`: lists what would be deleted, deletes nothing.

### `--force-init`

Re-runs a package's `package_init` unconditionally, ignoring the stamp.

---

## Selecting profilers

Resolved **per package**, in this order:

1. `--profiler <name>` — exactly those, **forced**. A profiler not in that
   package's `PACKAGE_PROFILERS` still runs; you get a warning on stderr and
   `"forced": true` in that run's `meta.json`.
2. `--profiler all` — that package's `PACKAGE_PROFILERS`.
3. `PACKAGE_PROFILERS` empty — `DEFAULT_PROFILERS` from `config.env`.
4. That empty too — every discovered profiler.

So `--package all --profiler all` is *not* a cartesian product: a package
listing two profilers gets two runs while its neighbour listing three gets
three.

**Order.** Packages run before profilers vary, and each dimension is ordered
the same way: as given on the command line, or alphabetically for `all`. So
`--profiler viztracer,time` runs viztracer first, while `--profiler all` runs
a package's list alphabetically regardless of how it is written in
`PACKAGE_PROFILERS`. Duplicates in that list are collapsed. The `profilers`
field of `--list-packages --json` is exactly what `--profiler all` will run,
in the same order, so a CI matrix built from it matches the harness.

### Kind compatibility

Every profiler declares which `PACKAGE_KIND`s it can handle. If a package's
kind is not in that list the run is **skipped**: an informational line, a
`"status": "skipped"` entry in `summary.json`, and no effect on the exit code.
Skips are a normal outcome, not a failure.

---

## Environment precedence

For each (package, profiler) run the environment is built by sourcing, in
order:

1. `config.env`
2. `profilers/<profiler>/.env`
3. `packages/<package>/.env`
4. the real process environment (highest priority)

Later layers win. **Layer 3 above layer 2 is the point**: it is how a package
tunes a profiler for itself.

```bash
# in packages/my-tool/.env
LINE_PROFILER_TARGETS="my_tool.core"   # configure line-profiler for this package
PYSPY_NATIVE=1                         # this workload has C extensions
VIZTRACER_MAX_DEPTH=32                 # its call graph is deep
```

Layer 4 means anything you export overrides the files, which is how CI tweaks
a run without editing anything:

```bash
PACKAGE_ARGS="--input data/big.json --iterations 50" \
  ./run_profiling.sh --package my-tool --profiler py-spy
```

`.env` files are sourced by **bash**, not parsed, so `$VAR` interpolation,
command substitution and `PATH="$MY_BIN:$PATH"` all behave as written. A
non-zero exit from the sourcing subshell is a hard error naming the file.

> **One exception to layer 4.** PATH-like variables (`PATH`, `PYTHONPATH`,
> `LD_LIBRARY_PATH`, …) are conventionally *extended* rather than assigned. If
> a `.env` layer changed one relative to what it inherited, that change is
> kept — otherwise `PYTHONPATH="$MY_SRC:$PYTHONPATH"` in a package `.env`
> could never take effect. Every other variable follows the rule above
> exactly.

---

## The target contract

This is the mechanism everything else rests on. Profilers come in two shapes:

* **Prefix wrappers** — `time`, `py-spy`. They run the complete command:
  `/usr/bin/time -v -- python -m tool`.
* **Interpreter replacements** — `viztracer`, `kernprof`. The tool *is* the
  interpreter: `viztracer -m tool`, never `viztracer python -m tool`.

So the workload is exposed in two forms. Before invoking the profiler hook the
runner writes `<RUN_DIR>/target.sh` and the harness sources it — a sourced
file of bash arrays sidesteps every quoting and export problem:

```bash
TARGET_KIND=python-module
TARGET_PYTHON=/repo/.venv/bin/python
TARGET_ARGV=(/repo/.venv/bin/python -m my_tool.cli --input data/sample.json)
TARGET_PYTHON_ARGV=(-m my_tool.cli --input data/sample.json)
```

| Variable | Meaning |
|---|---|
| `TARGET_ARGV` | the complete plain command — what you'd run with no profiling |
| `TARGET_PYTHON_ARGV` | the same minus the interpreter, i.e. Python's own trailing CLI shape. **Unset when `PACKAGE_KIND=exec`.** |
| `TARGET_PYTHON` | the interpreter path (unset for `exec`) |
| `TARGET_KIND` | the package's `PACKAGE_KIND` |

Both `viztracer` and `kernprof` accept `-m module args…` or `script.py args…`,
so `TARGET_PYTHON_ARGV` drops straight in after their flags.

A profiler that needs a Python target calls `require_python_target` first, so
an `exec` package produces a readable error rather than an empty array
expanding into a baffling complaint from the tool's own CLI parser.

Also exported into every hook: `RUN_DIR`, `RUN_ID`, `PACKAGE_NAME`,
`PROFILER_NAME`, `PROFILING_ROOT`, `REPO_ROOT`, `PACKAGE_WORKDIR`.

---

## Adding a package

A package is a workload you want to measure.

**1. Copy the template.**

```bash
cp -r packages/_template packages/my-service
```

The name must match `[a-z0-9][a-z0-9._-]*`. A leading `_` hides a directory
from discovery, which is how `_template` stays out of the listings.

**2. Describe the workload in `packages/my-service/.env`.**

```bash
PACKAGE_DESCRIPTION="Ingest 10k documents"
PACKAGE_KIND=python-module
PACKAGE_ENTRY=my_service.bench
PACKAGE_ARGS="--corpus data/10k --workers 4"
PACKAGE_WORKDIR="$REPO_ROOT"
PACKAGE_PYTHON="$REPO_ROOT/.venv/bin/python"
PACKAGE_PROFILERS="time py-spy line-profiler"
PACKAGE_INIT_FINGERPRINT="pyproject.toml uv.lock"
PACKAGE_TIMEOUT=600
LINE_PROFILER_TARGETS="my_service.index"
```

**3. Add hooks in `packages/my-service/package.sh`** (all optional):

```bash
package_init()     { uv sync --frozen; }   # one-time build
package_pre_run()  { rm -rf .cache; }      # before each profiled run
package_post_run() { :; }                  # after each run, even on failure
```

**4. Check and run.**

```bash
./run_profiling.sh --list-packages
./run_profiling.sh --package my-service --profiler all --dry-run
./run_profiling.sh --package my-service --profiler all
```

### Package `.env` reference

| Key | Meaning |
|---|---|
| `PACKAGE_DESCRIPTION` | one line, shown by `--list-packages` |
| `PACKAGE_KIND` | `python-module`, `python-script` or `exec` |
| `PACKAGE_ENTRY` | module name, script path, or executable path, per kind |
| `PACKAGE_ARGS` | arguments, split with `shlex.split` (normal shell quoting) |
| `PACKAGE_WORKDIR` | cwd for the workload and every hook |
| `PACKAGE_PYTHON` | interpreter for the `python-*` kinds (default `python3`) |
| `PACKAGE_PROFILERS` | curated allowlist; empty falls back to `DEFAULT_PROFILERS` |
| `PACKAGE_INIT_FINGERPRINT` | extra files feeding the init fingerprint, relative to `PACKAGE_WORKDIR` |
| `PACKAGE_TIMEOUT` | seconds; `0` or empty means no timeout |
| `PACKAGE_APT_PACKAGES` / `PACKAGE_PIP_PACKAGES` | dependencies for `install.sh` |

### When `.env` isn't enough

Define `package_command` in `package.sh` to build the argv yourself. It
overrides the `.env`-declared entry point completely:

```bash
package_command() {
    TARGET_PYTHON="$REPO_ROOT/.venv/bin/python"
    TARGET_PYTHON_ARGV=(-m my_service.bench --seed "$BENCH_SEED")
    TARGET_ARGV=("$TARGET_PYTHON" "${TARGET_PYTHON_ARGV[@]}")
}
```

Populate **both** arrays for a Python workload so prefix wrappers and
interpreter-replacing profilers both keep working. `--dry-run` calls this
hook too, so the printed command reflects the override.

---

## Adding a profiler

```bash
cp -r profilers/_template profilers/perf
```

Declare it in `profilers/perf/.env`:

```bash
PROFILER_DESCRIPTION="Linux perf sampling"
PROFILER_KINDS="python-module python-script exec"
PROFILER_FLAMEGRAPH=0
PROFILER_REQUIRES_BIN="perf"
PROFILER_APT_PACKAGES="linux-tools-generic"
PERF_FREQ=999
```

Implement it in `profilers/perf/profiler.sh`:

```bash
_perf_build_cmd() {
    cmd=(perf record -F "${PERF_FREQ:-999}" -g
         -o "$(run_artifact perf.data)" -- "${TARGET_ARGV[@]}")
}

profiler_wrap() {              # REQUIRED
    require_bin perf
    local cmd; _perf_build_cmd
    "${cmd[@]}"                # its exit status IS the workload's
}

profiler_post() {              # OPTIONAL, only after a successful wrap
    perf report -i "$(run_artifact perf.data)" > "$(run_artifact report.txt)"
}

profiler_dry_run() {           # OPTIONAL, for exact --dry-run output
    local cmd; _perf_build_cmd
    profiling_show_command "${cmd[@]}"
}
```

`install.sh` and `install.sh --check` now cover it, and it appears in
`--list-profilers`. No core change is needed — which is the whole point of the
`exec` kind being plumbed through even though nothing ships using it yet.

### Profiler `.env` reference

| Key | Meaning |
|---|---|
| `PROFILER_DESCRIPTION` | one line, shown by `--list-profilers` |
| `PROFILER_KINDS` | `PACKAGE_KIND`s this profiler supports; others are skipped |
| `PROFILER_FLAMEGRAPH` | `1` if it can emit a flamegraph |
| `PROFILER_FLAMEGRAPH_FILE` | path relative to `RUN_DIR`, recorded in `meta.json` when present |
| `PROFILER_REQUIRES_BIN` | binaries checked by `install.sh --check` and before each run |
| `PROFILER_APT_PACKAGES` / `PROFILER_PIP_PACKAGES` | dependencies for `install.sh` |
| `PROFILER_WARN_IF_UNSET` | variables whose absence should produce a warning |
| `PROFILER_WARN_MESSAGE` | text appended to that warning |

### Helpers available in hooks

From `lib/common.sh`, sourced before every hook:

| Helper | Purpose |
|---|---|
| `run_artifact <name>` | absolute path inside `RUN_DIR` |
| `require_bin <bin>…` | abort with a clear message if a tool is missing |
| `require_python_target` | abort unless the target is a Python program |
| `profiling_flamegraphs_enabled` | honour the global flamegraph kill switch |
| `profiling_show_command <argv>` | emit an argv for `--dry-run` |
| `profiling_warn` / `profiling_error` / `profiling_die` | logging |

---

## Package init

Some packages need a one-time build before they can be profiled. Define
`package_init` in `package.sh` and the harness runs it at most once per
invocation, then caches the result against a fingerprint:

```
fingerprint = sha256( packages/<pkg>/.env
                    + packages/<pkg>/package.sh
                    + contents of each PACKAGE_INIT_FINGERPRINT file )
```

| Condition | Result |
|---|---|
| no `package_init` defined | skip |
| `--force-init` | run |
| no stamp (fresh container) | run |
| fingerprint changed (lockfile, entry point, …) | run |
| previous attempt failed | run |
| otherwise | skip |

Notes that matter in practice:

* Fingerprint paths resolve relative to `PACKAGE_WORKDIR`. **A listed file
  that does not exist is a hard error**, not an empty hash — otherwise a typo
  would mean init silently never re-runs again.
* Output goes to `$PROFILING_STATE_DIR/<package>/init.log`, never into a run
  directory, so pruning artifacts can never trigger a rebuild.
* A failed init is recorded as `"status": "failed"`, skips that package's
  runs, and yields exit code 4. A failed build is never cached as done.
* `$PROFILING_STATE_DIR` is where "already built" lives. Mount it as a volume
  to keep builds across containers; delete it to force a clean rebuild.

---

## Output

```
output/
  <package>/<profiler>/<run-id>/
      meta.json  target.sh  stdout.log  stderr.log  <profiler artifacts>
  <package>/<profiler>/latest -> <run-id>      # relative symlink
  summary.json                                 # last invocation, overwritten
```

`run-id` is `YYYYmmdd-HHMMSS-<4 hex>`. Set the `RUN_ID` environment variable
to override it wholesale — a CI build number, say. Injected values are
sanitized down to `[A-Za-z0-9._-]`, so they cannot escape the output tree.

Console output is **tee'd**: you see the workload live and it is captured to
`stdout.log` / `stderr.log`.

`meta.json` per run:

```json
{
  "package": "my-tool", "profiler": "py-spy", "run_id": "20260729-141230-a3f1",
  "status": "ok", "exit_code": 0, "duration_s": 12.4,
  "started_at": "…", "finished_at": "…",
  "argv": ["…"], "kind": "python-module", "workdir": "…",
  "forced": false, "flamegraph": "profile.svg",
  "artifacts": ["profile.svg", "stdout.log", "stderr.log"],
  "host": "…", "git_sha": "…"
}
```

`status` is `ok`, `failed`, `timeout` or `skipped`. `git_sha` is best-effort
and simply absent when git is unavailable. `flamegraph` is null unless the
profiler declares `PROFILER_FLAMEGRAPH=1` *and* the file exists.

`summary.json` holds the invocation's arguments, per-status counts and the
array of per-run results, so a regression gate downstream has one file to
read.

Runs are **sequential** and each profiler is a **separate execution** of the
workload — they cannot be stacked.

### Interrupting a run

The workload runs in its own session so that a timeout can take the profiler
and everything it spawned down together. That also means a signal sent to the
harness does not reach it, so `SIGINT` (Ctrl-C), `SIGTERM` (a cancelled CI
job) and `SIGHUP` are handled explicitly: the harness kills the workload's
process group before exiting, and returns 1.

Termination escalates `SIGTERM` → `SIGKILL`, driven by whether the process
*group* is empty rather than by whether the direct child exited. Those differ:
GNU time sets `SIGTERM` to `SIG_IGN`, and an ignored disposition survives
`exec`, so `time -- sleep 99` leaves a `sleep` that shrugs off the signal that
killed its parent. There is a ~2 s courtesy window after `SIGTERM` for
workloads that clean up on it before `SIGKILL` lands.

Re-running a pinned `RUN_ID` clears that run directory first, so a rebuilt
`build-123` cannot report the previous attempt's artifacts as its own.

---

## Retention

```bash
./run_profiling.sh --remove-output                    # keep PROFILING_KEEP_DEFAULT
./run_profiling.sh --remove-output --keep 3
./run_profiling.sh --remove-output=all                # keep nothing
./run_profiling.sh --remove-output --package my-tool --profiler py-spy
./run_profiling.sh --remove-output --keep 3 --dry-run
```

A terminal action: prune, print, exit. It never combines with a run.

`--keep N` keeps N runs **per (package, profiler) pair**, not N in total — so
`--keep 3` on a package with four profilers leaves twelve run directories.
Scope follows `--package` / `--profiler`; with neither, the whole tree.

Safety rules:

* Directories whose names do not match the run-id pattern are **left
  untouched**, never guessed at. A custom `RUN_ID` therefore opts that run out
  of automatic pruning — delete those by hand.
* The `latest` symlink is re-pointed at the newest survivor, or removed when
  none is left.
* The harness refuses to prune when `PROFILING_OUTPUT_DIR` is unset, relative,
  the filesystem root, or suspiciously shallow — and re-checks that every
  candidate resolves inside it.

---

## Exit codes

| Code | Meaning |
|---|---|
| 0 | all runs succeeded (skips do not affect this) |
| 1 | at least one workload run failed or timed out |
| 2 | usage error: unknown package/profiler, missing `--profiler`, bad flag |
| 3 | a required profiler binary is missing |
| 4 | a package init failed |

The harness runs everything then aggregates — it does not stop at the first
failure — and reports the **highest** applicable code.

---

## `install.sh`

```bash
./install.sh              # install everything discovered
./install.sh --check      # CI preflight
./install.sh --dry-run    # print the commands without running them
```

It reads `*_APT_PACKAGES` and `*_PIP_PACKAGES` out of every `profilers/*/.env`
and `packages/*/.env`, deduplicates, and installs. Adding a profiler directory
extends the installer automatically.

* `sudo` is used only when not already root; apt is skipped with a warning
  when unavailable.
* On Debian/Ubuntu the system interpreter is marked externally managed
  (PEP 668); in a container that is exactly where these tools belong, so
  `--break-system-packages` is passed explicitly rather than failing.
* `--check` verifies every `PROFILER_REQUIRES_BIN`, prints a table, and exits
  3 listing what is missing.

---

## Running in a container

### py-spy needs ptrace

py-spy reads another process's memory, which needs `CAP_SYS_PTRACE`. Without
it you get a cryptic "Operation not permitted".

```bash
docker run --cap-add=SYS_PTRACE …
```

On a host with a hardened kernel you may also need:

```bash
sudo sysctl -w kernel.yama.ptrace_scope=0
```

`install.sh --check` warns when py-spy is installed but
`/proc/sys/kernel/yama/ptrace_scope` is non-zero, and the profiler repeats the
hint if a run fails. Check this first — it is the most common container
problem by a wide margin.

### Artifact sizes

**viztracer traces are large.** It records every call, and its stock defaults
produce roughly 100 MB of JSON from a workload that runs for a fraction of a
second. Two conservative defaults keep that in check:

```bash
VIZTRACER_MAX_DEPTH=64          # maximum call depth recorded
VIZTRACER_TRACER_ENTRIES=200000 # circular event buffer (~20 MB)
```

The buffer is circular, so overflowing it costs you the *earliest* events, not
the run. Raise either per package if you need more, but budget the disk — and
note that deterministic tracing also slows the workload down by one to two
orders of magnitude, so a viztracer run is not a timing measurement. Use
`--remove-output` in CI to keep the tree bounded.

### Persisting build state

`.state/` is what makes `package_init` run once instead of on every container
start. Mount it as a volume:

```bash
docker run -v profiling-state:/app/profiling/.state …
```

### Disk layout

Both `output/` and `.state/` are gitignored, and every path is defined in
`config.env`, so the whole tree can be relocated with one edit — or pointed at
a mounted volume via `PROFILING_OUTPUT_DIR` / `PROFILING_STATE_DIR`.

---

## Profiler notes

| Profiler | Kinds | Flamegraph | Notes |
|---|---|---|---|
| `time` | all three, incl. `exec` | no | GNU time `-v`: wall clock, CPU, peak RSS. Cheap enough to always run. |
| `py-spy` | python | **yes** | Sampling. Needs ptrace. See below. |
| `viztracer` | python | no | Deterministic timeline; view with `vizviewer <RUN_DIR>/trace.json`. Large artifacts, heavy overhead. |
| `line-profiler` | python | no | Per-line timings via `kernprof`. Needs configuration. |

**line-profiler is the one profiler that is not zero-config.** Without
`LINE_PROFILER_TARGETS` (or `@profile` decorators in the source) it produces
an empty report. Set it in the package `.env`:

```bash
LINE_PROFILER_TARGETS="my_tool.core"   # comma-separated modules/functions
```

The harness warns — it does not fail — when a package selects line-profiler
without setting it.

**py-spy starts the workload and attaches to it.** py-spy's exit status
describes *py-spy*, not the program it ran, and the two are uncorrelated.
Running one command repeatedly, `py-spy record -- <cmd>` returns 0 for a
workload that exited 3, and 1 for a workload that exited 0 — the latter
whenever its flamegraph renderer found no samples to plot, which depends on
how long the workload ran rather than on whether it worked:

```
child exits 0 -> py-spy exits  0 1 1 1 1 0 1 1 1 1
child exits 3 -> py-spy exits  1 0 1 1 1 1 1 0 0 1
```

Since `profiler_wrap`'s status *is* the workload's status, trusting py-spy's
would report broken workloads as successful and healthy ones as broken, at
random. So the profiler starts the workload itself and attaches py-spy to the
resulting pid; the shell then owns the process and `wait` yields its exact
exit code. The cost is that sampling begins a few milliseconds late, so the
very start of interpreter startup can be missed.

A non-zero py-spy status is still used, but only to tell its failure modes
apart, and never by trusting the number itself:

| What happened | How it is detected | Outcome |
|---|---|---|
| Attached, collected no samples | it still wrote an output file | warning; the workload's status stands |
| Could not attach, workload still running | no output file, and the workload was alive when py-spy quit | **run fails** — ptrace denied or similar |
| Could not attach, workload already finished | no output file, workload already gone | warning; the workload's status stands |

That last row is the attach-window race, and it is deliberately not a failure:
a workload that finishes in a few milliseconds occasionally beats the attach,
and failing on it would make fast packages flaky in CI. You lose the profile
for that run, not the run. The distinction between the last two rows is only
observable while it happens, which is why the profiler waits on py-spy first
and checks whether the workload is still live at that moment.

Set `PYSPY_CAPTURE_EXIT_CODE=0` for plain launch mode, where the workload's
exit status is simply not observable and is reported as 0.

**The global flamegraph kill switch.** `PROFILING_FLAMEGRAPHS=0` makes
flamegraph-capable profilers degrade to a cheaper format rather than fail —
py-spy falls back to `speedscope` JSON.

---

## Global configuration (`config.env`)

| Variable | Default | Meaning |
|---|---|---|
| `PROFILING_ROOT` | resolved automatically | this directory |
| `REPO_ROOT` | `$PROFILING_ROOT/..` | root of the project being profiled |
| `PROFILING_OUTPUT_DIR` | `$PROFILING_ROOT/output` | where artifacts go |
| `PROFILING_STATE_DIR` | `$PROFILING_ROOT/.state` | where init stamps go |
| `DEFAULT_PROFILERS` | `time py-spy` | fallback when `PACKAGE_PROFILERS` is empty |
| `PROFILING_KEEP_DEFAULT` | `1` | default `--keep` for `--remove-output` |
| `PROFILING_FLAMEGRAPHS` | `1` | global flamegraph kill switch |

Every one is overridable from the real environment.
