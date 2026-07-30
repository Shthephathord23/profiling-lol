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

profiling_warn() { printf 'WARN: %s\n' "$*" >&2; }
profiling_error() { printf 'ERROR: %s\n' "$*" >&2; }

# Print a message and abort the run with a distinctive exit status.
profiling_die() {
    profiling_error "$*"
    exit 78
}

# --------------------------------------------------------- target helpers ---

# Guard for profilers that *replace* the interpreter (viztracer, kernprof)
# rather than prefixing the command.  Call it first in profiler_command: without
# it an unset TARGET_PYTHON_ARGV expands to nothing and the tool reports a
# baffling error about its own arguments instead of the real problem.
require_python_target() {
    if [ "${TARGET_KIND:-}" = "exec" ] ||
        ! declare -p TARGET_PYTHON_ARGV >/dev/null 2>&1; then
        profiling_die \
            "profiler '${PROFILER_NAME:-?}' needs a Python target, but package" \
            "'${PACKAGE_NAME:-?}' has PACKAGE_KIND=${TARGET_KIND:-unset}." \
            "Use a prefix-style profiler (e.g. 'time') for this package, or" \
            "drop '${PROFILER_NAME:-?}' from its PACKAGE_PROFILERS."
    fi
}

# Absolute path inside the current run directory, e.g.
#   out=$(run_artifact profile.svg)
run_artifact() {
    printf '%s/%s\n' "${RUN_DIR:?RUN_DIR is not set}" "$1"
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
