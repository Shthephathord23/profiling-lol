# Profiling harness

Runs this project's workloads ("packages") under a set of profilers, writes
timestamped artifacts, and is meant to be driven from CI.

Packages and profilers are **discovered from the filesystem**: a directory
under `packages/` or `profilers/` containing a `.env` file is an entry, and
the listings, the installer and the CI matrix pick it up with no code change.
A leading `_` (as in `_template`) hides a directory from discovery; names
must match `[a-z0-9][a-z0-9._-]*`.

```
profiling/
  run_profiling.sh     entry point (a shim around lib/cli.py)
  install.sh           discovery-driven dependency installer
  config.env           global paths and defaults
  lib/                 the Python core and its bash halves
  packages/<name>/     .env + package.sh   -- a workload
  profilers/<name>/    .env + profiler.sh  -- a way to measure it

$PROFILING_OUT_PATH/
  output/<package>/<profiler>/<run-id>/   run artifacts
```

## Quick start

```bash
cd profiling
./install.sh                                   # install profiler dependencies
./install.sh --check                           # CI preflight: are the binaries there?

./run_profiling.sh --list-packages
./run_profiling.sh --package my-tool --profiler all
./run_profiling.sh --package my-tool --profiler all --dry-run
```

`packages/my-tool/` is a working example: small, stdlib-only, offline. It
runs in a fresh checkout, so you can exercise the harness before wiring up a
real project.

## Commands

```
run_profiling.sh --package <sel> --profiler <sel> [--dry-run]
                 [--env-package KEY=VALUE] [--env-profiler KEY=VALUE]
run_profiling.sh --init [--package <sel>] [--dry-run]
run_profiling.sh --list-packages  [--json]
run_profiling.sh --list-profilers [--json]
run_profiling.sh --remove-output[=all] [--package <sel>] [--profiler <sel>]
                                       [--keep N] [--dry-run]
```

`<sel>` is a name, a comma-separated list, a repeated flag, or the literal
`all`. `--profiler` is mandatory for a run — profiling every workload with
every profiler is expensive, so there is no implicit sweep. `--init`,
`--list-*` and `--remove-output` are terminal actions: they cannot be
combined with each other, and a flag that does not apply to the chosen
action is an error, not silently ignored.

`--dry-run` resolves everything — environment layering, `package_command`
overrides, the profiler's own argv — and prints the exact command line each
profiler would execute, creating and running nothing. The printed command is
produced by the same `profiler_command` builder the real run uses, so it
cannot drift from what executes.

`--list-* --json` emits a JSON array for generating a CI matrix. The
`profilers` field of `--list-packages --json` is exactly what
`--profiler all` will run, in the same order.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | all runs succeeded (skips do not affect this) |
| 1 | a run failed, timed out, or could not start (missing binary, failed init) |
| 2 | usage error: unknown package/profiler, missing `--profiler`, bad flag |

The harness runs everything then aggregates — it does not stop at the first
failure.

## Selecting profilers

Resolved per package:

1. `--profiler <name>` — exactly those, whether or not the package lists them.
2. `--profiler all` — the package's `PACKAGE_PROFILERS`.
3. That empty — `DEFAULT_PROFILERS` from `config.env`.
4. That empty too — every discovered profiler.

Explicit names keep their command-line order; anything reached through `all`
runs alphabetically, duplicates collapsed. An unknown name errors
immediately, blaming the file it came from. A profiler that does not support
the package's `PACKAGE_KIND` is **skipped**: an informational line and a
`"skipped"` entry in `summary.json`, never a failure.

## Environment

Each run's environment is layered, later layers winning:

1. the ambient process environment — the base, not the winner
2. `config.env`
3. `profilers/<profiler>/.env`
4. `packages/<package>/.env`
5. `--env-profiler` / `--env-package` — the command line always wins

The package sits **above** the profiler on purpose: that is how a package
tunes a profiler for itself:

```bash
# in packages/my-tool/.env
LINE_PROFILER_TARGETS="my_tool.core"
PYSPY_NATIVE=1
VIZTRACER_MAX_DEPTH=32
```

`.env` files are sourced by bash, so `$VAR` interpolation, command
substitution and `PATH="$X:$PATH"` behave as written. A `.env` must not call
`exit`, and any failure while sourcing is a hard error naming the file.
Because a `.env` assigns unconditionally, a stray exported variable cannot
silently redirect a run; harness knobs in `config.env` use
`: "${VAR:=default}"` and therefore *do* answer to the environment
(`PROFILING_OUT_PATH=/var/lib/profiling ./run_profiling.sh …`,
`docker run -e DEFAULT_PROFILERS=time …`).

`--env-package` / `--env-profiler` override any knob for one invocation
without editing a file. Both land on the topmost layer (`--env-package` wins
a collision) and are recorded separately in `meta.json` under
`env_overrides`, so a run says what was overridden and at which level.

```bash
./run_profiling.sh --package my-tool --profiler py-spy \
  --env-package PACKAGE_ARGS="--input data/big.json" --env-profiler PYSPY_RATE=500
```

## The target contract

Profilers come in two shapes: **prefix wrappers** (`time`, `py-spy`) that run
the complete command, and **interpreter replacements** (`viztracer`,
`kernprof`) that *are* the interpreter. The runner writes
`<RUN_DIR>/target.sh` declaring the workload in both forms as bash arrays,
and the harness sources it before the profiler hooks run:

| Variable | Meaning |
|---|---|
| `TARGET_ARGV` | the complete plain command — what you'd run with no profiling |
| `TARGET_PYTHON_ARGV` | the same minus the interpreter (`-m module args…`). **Unset when `PACKAGE_KIND=exec`** |
| `TARGET_PYTHON` | the interpreter path (unset for `exec`) |
| `TARGET_KIND` | the package's `PACKAGE_KIND` |

A profiler that needs a Python target calls `require_python_target` first,
so an `exec` package produces a readable error instead of an empty array
confusing the tool's own CLI parser.

Also exported into every hook: `RUN_DIR`, `RUN_ID`, `PACKAGE_NAME`,
`PROFILER_NAME`, `PROFILING_ROOT`, `REPO_ROOT`, `PACKAGE_WORKDIR`, plus
`PACKAGE_DIR` and `PROFILER_DIR` — each hook's own directory, so a profiler
can invoke a helper script that lives beside it.

## Adding a package

```bash
cp -r packages/_template packages/my-service
```

Describe the workload in `packages/my-service/.env`:

```bash
PACKAGE_DESCRIPTION="Ingest 10k documents"
PACKAGE_KIND=python-module           # python-module | python-script | exec
PACKAGE_ENTRY=my_service.bench      # module name, script path, or executable
PACKAGE_ARGS="--corpus data/10k --workers 4"
PACKAGE_WORKDIR="$REPO_ROOT"
PACKAGE_PYTHON="$REPO_ROOT/.venv/bin/python"
PACKAGE_PROFILERS="time py-spy line-profiler"
PACKAGE_INIT=1
PACKAGE_TIMEOUT=600
```

Optional hooks in `packages/my-service/package.sh`:

```bash
package_init()     { [ -x .venv/bin/python ] || python3 -m venv .venv; }
package_pre_run()  { rm -rf .cache; }   # before each profiled run
package_post_run() { :; }               # after each run, even on failure
```

| Key | Meaning |
|---|---|
| `PACKAGE_DESCRIPTION` | one line, shown by `--list-packages` |
| `PACKAGE_KIND` | `python-module`, `python-script` or `exec` |
| `PACKAGE_ENTRY` | module name, script path, or executable path, per kind |
| `PACKAGE_ARGS` | arguments, split with `shlex.split` (normal shell quoting) |
| `PACKAGE_WORKDIR` | cwd for the workload and every hook |
| `PACKAGE_PYTHON` | interpreter for the `python-*` kinds (default `python3`) |
| `PACKAGE_PROFILERS` | curated allowlist; empty falls back to `DEFAULT_PROFILERS` |
| `PACKAGE_INIT` | `1` = call `package_init` before this package's runs |
| `PACKAGE_TIMEOUT` | seconds; `0` or empty means no timeout |
| `PACKAGE_APT_PACKAGES` / `PACKAGE_PIP_PACKAGES` | dependencies for `install.sh` |

When `.env` isn't enough, define `package_command` in `package.sh` to build
the argv yourself. It overrides the declared entry point completely —
populate **both** arrays so both profiler shapes keep working, and
`--dry-run` reflects it too:

```bash
package_command() {
    TARGET_PYTHON="$REPO_ROOT/.venv/bin/python"
    TARGET_PYTHON_ARGV=(-m my_service.bench --seed "$BENCH_SEED")
    TARGET_ARGV=("$TARGET_PYTHON" "${TARGET_PYTHON_ARGV[@]}")
}
```

## Adding a profiler

```bash
cp -r profilers/_template profilers/perf
```

Declare it in `profilers/perf/.env`, implement it in
`profilers/perf/profiler.sh`:

```bash
profiler_command() {           # REQUIRED -- declare the command, run nothing
    cmd=(perf record -F "${PERF_FREQ:-999}" -g
         -o "$(run_artifact perf.data)" -- "${TARGET_ARGV[@]}")
}

profiler_post() {              # OPTIONAL, only after the command succeeded
    perf report -i "$(run_artifact perf.data)" > "$(run_artifact report.txt)"
}
```

A profiler declares a **command**; the harness runs it. The command is one
argv — if a profiler needs two processes, a wait, or any sequencing, that
belongs in a small script inside the profiler's own directory, invoked as
`"$PROFILER_DIR/<script>"` (see `profilers/py-spy/attach.sh`). Deleting the
profiler's directory then deletes all of it. `install.sh`, `--check` and the
listings cover a new profiler automatically.

| Key | Meaning |
|---|---|
| `PROFILER_DESCRIPTION` | one line, shown by `--list-profilers` |
| `PROFILER_KINDS` | `PACKAGE_KIND`s this profiler supports; others are skipped |
| `PROFILER_FLAMEGRAPH` | `1` if it can emit a flamegraph |
| `PROFILER_FLAMEGRAPH_FILE` | path relative to `RUN_DIR`, recorded in `meta.json` when present |
| `PROFILER_REQUIRES_BIN` | binaries checked by `install.sh --check` and before each run |
| `PROFILER_APT_PACKAGES` / `PROFILER_PIP_PACKAGES` | dependencies for `install.sh` |

Helpers from `lib/common.sh`, available in every hook:

| Helper | Purpose |
|---|---|
| `run_artifact <name>` | absolute path inside `RUN_DIR` |
| `require_bin <bin>…` | abort with a clear message if a tool is missing |
| `require_python_target` | abort unless the target is a Python program |
| `profiling_warn` / `profiling_error` / `profiling_die` | logging |

## Package init

`PACKAGE_INIT=1` means: call `package_init` once, before this package's
runs. There is no staleness tracking — the hook runs on every invocation,
and guarding the expensive part is the hook's own job (one line usually
does it; only the package knows what "already built" means for it).

`--init` is the manual trigger. It **ignores `PACKAGE_INIT`** — asking for it
is already saying you want it now — and skips packages without the hook, so
it can be pointed at anything. The reverse, `PACKAGE_INIT=1` with no hook,
*is* an error. A failed init records that package's runs as failed (exit 1);
nothing is cached, so the next invocation simply tries again. `install.sh`
runs `--init` after installing dependencies.

## Output

```
output/
  <package>/<profiler>/<run-id>/
      meta.json  target.sh  stdout.log  stderr.log  <profiler artifacts>
  <package>/<profiler>/latest -> <run-id>      # relative symlink
  summary.json                                 # last invocation, overwritten
```

`run-id` is `YYYYmmdd-HHMMSS-<4 hex>`. Set the `RUN_ID` environment variable
(a CI build number, say) to override it; injected values are sanitized down
to `[A-Za-z0-9._-]`, and re-running a pinned `RUN_ID` clears that run
directory first so a rebuilt job cannot report the previous attempt's
artifacts as its own.

Console output is tee'd: you see the workload live and it is captured to
`stdout.log` / `stderr.log`. Runs are sequential, and each profiler is a
separate execution of the workload — they cannot be stacked.

`meta.json` records each run: `status` (`ok | failed | timeout | skipped`),
`exit_code`, duration, timestamps, the resolved workload `argv` and profiler
`command`, `kind`, `workdir`, the artifact list, `host`, best-effort
`git_sha`, `flamegraph` (when the profiler declares one and the file
exists), `reason` (on failure) and `env_overrides` (when `--env-*` was
used). `summary.json` holds the invocation's arguments, per-status counts
and the array of per-run records — one file for a downstream gate to read.

### Interrupting a run

The workload runs in its own session so that a timeout can take the profiler
and everything it spawned down together. A signal to the harness therefore
does not reach it, so `SIGINT`, `SIGTERM` (a cancelled CI job) and `SIGHUP`
are handled explicitly: the harness kills everything in the workload's
session before exiting, and returns 1.

Termination escalates `SIGTERM` → `SIGKILL` over every live process in the
session, enumerated from `/proc` — not the process group, which was observed
to be unreliable (bash can leave a profiler's command in a different group,
and a zombie keeps a group looking alive). There is a ~2 s courtesy window
after `SIGTERM` for workloads that clean up on it.

## Retention

```bash
./run_profiling.sh --remove-output                    # keep PROFILING_KEEP_DEFAULT
./run_profiling.sh --remove-output --keep 3
./run_profiling.sh --remove-output=all                # keep nothing
./run_profiling.sh --remove-output --package my-tool --profiler py-spy --dry-run
```

Keeps the newest N runs **per (package, profiler) pair**, scope following
`--package` / `--profiler`. Every directory inside a pair is treated as a
run, whatever it is called, ordered by **mtime** — run ids are only
chronological when the harness generated them. The `latest` symlink is
re-pointed at the newest survivor. As a guard against a mis-set variable
feeding `rm -rf`, pruning refuses to run when `PROFILING_OUTPUT_DIR` is
unset, relative, or suspiciously shallow.

## install.sh

```bash
./install.sh              # install everything discovered
./install.sh --check      # CI preflight; exits 1 listing what is missing
./install.sh --dry-run    # print the commands without running them
```

Reads `*_APT_PACKAGES` and `*_PIP_PACKAGES` out of every `.env`,
deduplicates, installs (using `sudo` only when not root, and
`--break-system-packages` on PEP 668 systems, which in a container is where
these tools belong), runs `--init`, then verifies. Adding a profiler
directory extends it automatically.

## Running in a container

**py-spy needs ptrace.** Run with `docker run --cap-add=SYS_PTRACE`; on a
hardened host you may also need `sudo sysctl -w kernel.yama.ptrace_scope=0`.
`install.sh --check` warns when py-spy is installed but the yama scope is
non-zero — check this first, it is the most common container problem by a
wide margin.

**viztracer traces are large.** It records every call; the shipped defaults
(`VIZTRACER_MAX_DEPTH=64`, `VIZTRACER_TRACER_ENTRIES=200000`, a circular
buffer of roughly 20 MB) keep that bounded. Overflow costs the *earliest*
events, not the run. Deterministic tracing also slows the workload by one to
two orders of magnitude, so a viztracer run is not a timing measurement.

**Point `PROFILING_OUT_PATH` outside the repository** so profiling data can
never be captured by a commit and survives a re-clone:

```bash
PROFILING_OUT_PATH=/var/lib/profiling ./run_profiling.sh --package all --profiler all
docker run -v /var/lib/profiling:/var/lib/profiling -e PROFILING_OUT_PATH=/var/lib/profiling …
```

It defaults to the harness's own directory (with `output/` gitignored) so a
fresh checkout runs with no configuration. There is no state directory to
persist: to skip `package_init` work across containers, persist the thing
the hook builds (the virtualenv, the compiled tree).

## Profiler notes

| Profiler | Kinds | Flamegraph | Notes |
|---|---|---|---|
| `time` | all three, incl. `exec` | no | GNU time `-v`: wall clock, CPU, peak RSS. Cheap enough to always run. |
| `py-spy` | python | **yes** | Sampling. Needs ptrace. See below. |
| `viztracer` | python | no | Deterministic timeline; view with `vizviewer <RUN_DIR>/trace.json`. Large artifacts, heavy overhead. |
| `line-profiler` | python | no | Per-line timings via `kernprof`. Needs configuration. |

**line-profiler is the one profiler that is not zero-config**: without
`LINE_PROFILER_TARGETS` in the package `.env` (or `@profile` decorators in
the source) it produces an empty report. The profiler warns — it does not
fail — when that happens.

**py-spy runs through `profilers/py-spy/attach.sh`**, which starts the
workload itself and attaches py-spy to the resulting pid. py-spy's own exit
status describes py-spy, not the program it ran, and the two are measurably
uncorrelated — it returns 0 for workloads that failed and 1 for healthy
workloads too short to sample. Owning the process lets the shell recover the
workload's exact exit code; the cost is a few milliseconds of missed
interpreter startup. A non-zero py-spy status is downgraded to a warning
unless the workload was still running when py-spy gave up — a real attach
failure (ptrace denied), which fails the run. The attach-window race on very
fast workloads costs you the profile, not the run. Set
`PYSPY_CAPTURE_EXIT_CODE=0` for plain launch mode, where the workload's
status is not observable and is reported as 0. Full reasoning and
measurements in `attach.sh`.

## Global configuration (`config.env`)

| Variable | Default | Meaning |
|---|---|---|
| `PROFILING_ROOT` | resolved automatically | this directory |
| `REPO_ROOT` | `$PROFILING_ROOT/..` | root of the project being profiled |
| `PROFILING_OUT_PATH` | `$PROFILING_ROOT` | base for artifacts — **point this outside the repo** |
| `PROFILING_OUTPUT_DIR` | `$PROFILING_OUT_PATH/output` | where artifacts go |
| `DEFAULT_PROFILERS` | `time py-spy` | fallback when `PACKAGE_PROFILERS` is empty |
| `PROFILING_KEEP_DEFAULT` | `1` | default `--keep` for `--remove-output` |

Every one is declared with `: "${VAR:=default}"`, so an exported value from
the environment wins.
