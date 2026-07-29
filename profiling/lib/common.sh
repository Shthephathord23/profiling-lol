# Helpers sourced into every package.sh / profiler.sh hook.
#
# This file is sourced, never executed.  Keep it side-effect free: defining
# functions only, so that sourcing it during --dry-run costs nothing.
#
# Available in every hook (exported by the runner):
#   RUN_DIR RUN_ID PACKAGE_NAME PROFILER_NAME PROFILING_ROOT REPO_ROOT
#   PACKAGE_WORKDIR
# Plus the target contract, sourced from "$RUN_DIR/target.sh":
#   TARGET_KIND TARGET_PYTHON TARGET_ARGV[] TARGET_PYTHON_ARGV[]

# ---------------------------------------------------------------- logging ---

profiling_log() { printf '%s\n' "$*" >&2; }
profiling_warn() { printf 'WARN: %s\n' "$*" >&2; }
profiling_error() { printf 'ERROR: %s\n' "$*" >&2; }

# Print a message and abort the run with a distinctive exit status.
profiling_die() {
    profiling_error "$*"
    exit 78
}

# --------------------------------------------------------- target helpers ---

# True when the workload is a Python program, i.e. TARGET_PYTHON_ARGV is set.
profiling_has_python_target() {
    [ "${TARGET_KIND:-}" != "exec" ] &&
        declare -p TARGET_PYTHON_ARGV >/dev/null 2>&1
}

# Guard for profilers that *replace* the interpreter (viztracer, kernprof)
# rather than prefixing the command.  Call it first in profiler_wrap: without
# it an unset TARGET_PYTHON_ARGV expands to nothing and the tool reports a
# baffling error about its own arguments instead of the real problem.
require_python_target() {
    if ! profiling_has_python_target; then
        profiling_die \
            "profiler '${PROFILER_NAME:-?}' needs a Python target, but package" \
            "'${PACKAGE_NAME:-?}' has PACKAGE_KIND=${TARGET_KIND:-unset}." \
            "Use a prefix-style profiler (e.g. 'time') for this package, or" \
            "drop '${PROFILER_NAME:-?}' from its PACKAGE_PROFILERS."
    fi
}

# 1 when flamegraph output is globally enabled, 0 when the kill switch is set.
# Flamegraph-capable profilers should degrade to a cheaper output format when
# this is 0 rather than failing the run.
profiling_flamegraphs_enabled() {
    [ "${PROFILING_FLAMEGRAPHS:-1}" = "1" ]
}

# Absolute path inside the current run directory, e.g.
#   out=$(run_artifact profile.svg)
run_artifact() {
    printf '%s/%s\n' "${RUN_DIR:?RUN_DIR is not set}" "$1"
}

# ------------------------------------------------------------- --dry-run ---

# Emit an argv for --dry-run, NUL-separated so no quoting can be lost.
# Use it from the optional `profiler_dry_run` hook:
#
#   profiler_dry_run() { mytool_build_cmd; profiling_show_command "${cmd[@]}"; }
#
# Share one argv builder between profiler_wrap and profiler_dry_run and the
# printed command is guaranteed to be the one that would really run.
profiling_show_command() {
    printf '%s\0' "$@"
}

# Fail early with a clear message when a tool the hook needs is not installed.
require_bin() {
    local bin
    for bin in "$@"; do
        case "$bin" in
            /*) [ -x "$bin" ] && continue ;;
            *) command -v "$bin" >/dev/null 2>&1 && continue ;;
        esac
        profiling_die "required binary not found: $bin (run install.sh)"
    done
}
