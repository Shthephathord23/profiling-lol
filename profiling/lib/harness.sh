#!/usr/bin/env bash
#
# Resolve and run one (package, profiler) pair.  Invoked by lib/runner.py, which
# exports everything below; it is not meant to be run by hand.
#
# One harness serves --dry-run and the real run alike.  It sources the same
# files either way and calls the same profiler_command builder, so the command
# --dry-run prints is by construction the command that executes -- they cannot
# drift, because there is only one of them.
#
# With PROFILING_RESOLVE_ONLY=1 it stops right after writing what it resolved.
# Otherwise it goes on to run the workload, all in this one bash process so that
# the hooks share shell state and package_post_run can be an EXIT trap that
# fires even when the run fails or is killed.
#
# Expected in the environment:
#   PROFILING_ROOT PROFILING_TARGET_FILE PROFILING_PACKAGE_SH
#   PROFILING_PROFILER_SH PROFILING_ARGV_FILE PROFILING_COMMAND_FILE
#   PACKAGE_WORKDIR PROFILER_NAME  (see runner._hook_env)
#
# Exit codes it originates:
#   77  PACKAGE_WORKDIR does not exist
#   78  a definition file is broken or a hook contract is unmet

set -o pipefail
# shellcheck source=common.sh
. "$PROFILING_ROOT/lib/common.sh"
# shellcheck source=/dev/null
. "$PROFILING_TARGET_FILE"

# A definition file that will not source is a hard error.  Sourced at the top
# level, never inside a function, so `declare` in a hook still lands globally.
# Without the status check bash prints the syntax error and carries on, and the
# run then proceeds with every hook silently missing -- including the
# package_pre_run guards whose whole job is to fail loudly.
# shellcheck source=/dev/null
if [ -f "$PROFILING_PACKAGE_SH" ] && ! . "$PROFILING_PACKAGE_SH"; then
    profiling_error "package.sh failed to source (see the error above): $PROFILING_PACKAGE_SH"
    exit 78
fi
# shellcheck source=/dev/null
if [ -f "$PROFILING_PROFILER_SH" ] && ! . "$PROFILING_PROFILER_SH"; then
    profiling_error "profiler.sh failed to source (see the error above): $PROFILING_PROFILER_SH"
    exit 78
fi

# The package may rewrite the workload entirely.
if declare -F package_command >/dev/null; then
    package_command
fi
printf '%s\0' "${TARGET_ARGV[@]}" >"$PROFILING_ARGV_FILE"

if ! declare -F profiler_command >/dev/null; then
    profiling_error "profiler '$PROFILER_NAME' defines no profiler_command function"
    exit 78
fi

cmd=()
profiler_command
# An empty cmd expands to nothing, so the run would "succeed" in zero seconds
# having executed no profiler and no workload.  Checked before the command file
# is written, so --dry-run rejects it on the same path as a real run.
if [ "${#cmd[@]}" -eq 0 ]; then
    profiling_error "profiler '$PROFILER_NAME' resolved to an empty command;" \
        "profiler_command must populate the 'cmd' array"
    exit 78
fi
printf '%s\0' "${cmd[@]}" >"$PROFILING_COMMAND_FILE"

if [ "${PROFILING_RESOLVE_ONLY:-0}" = "1" ]; then
    exit 0
fi

__profiling_post_run() {
    if declare -F package_post_run >/dev/null; then
        package_post_run || profiling_warn "package_post_run exited $?"
    fi
}
trap __profiling_post_run EXIT

cd "$PACKAGE_WORKDIR" || {
    profiling_error "PACKAGE_WORKDIR does not exist: $PACKAGE_WORKDIR"
    exit 77
}

if declare -F package_pre_run >/dev/null; then
    package_pre_run || {
        __profiling_status=$?
        profiling_error "package_pre_run exited $__profiling_status"
        exit "$__profiling_status"
    }
fi

"${cmd[@]}"
__profiling_status=$?

if [ "$__profiling_status" -eq 0 ] && declare -F profiler_post >/dev/null; then
    profiler_post || {
        __profiling_status=$?
        profiling_error "profiler_post exited $__profiling_status"
    }
fi

exit "$__profiling_status"
