#!/usr/bin/env bash
# Resolve -- and, unless PROFILING_RESOLVE_ONLY=1, run -- one (package,
# profiler) pair.  Invoked by lib/runner.py with the layered environment
# exported; see _hook_env there for the contract.
#
# One harness serves --dry-run and the real run alike: same sourced files,
# same profiler_command builder, so what --dry-run prints cannot drift from
# what executes.  Everything runs in this one bash process, so hooks share
# shell state and package_post_run can be an EXIT trap.
#
# Exit codes 77/78 mark harness-detected configuration errors; anything else
# is the workload's own status.

set -o pipefail
. "$PROFILING_ROOT/lib/common.sh"
. "$PROFILING_TARGET_FILE"
if [ -f "$PROFILING_PACKAGE_SH" ]; then . "$PROFILING_PACKAGE_SH"; fi
if [ -f "$PROFILING_PROFILER_SH" ]; then . "$PROFILING_PROFILER_SH"; fi

# The package may rewrite the workload entirely.
if declare -F package_command >/dev/null; then
  package_command
fi
if [ "${#TARGET_ARGV[@]}" -eq 0 ]; then
  profiling_error "package_command left TARGET_ARGV empty"
  exit 78
fi
printf '%s\0' "${TARGET_ARGV[@]}" > "$PROFILING_ARGV_FILE"

if ! declare -F profiler_command >/dev/null; then
  profiling_error "profiler '$PROFILER_NAME' defines no profiler_command function"
  exit 78
fi

cmd=()
profiler_command
if [ "${#cmd[@]}" -eq 0 ]; then
  # An empty array would expand to zero words below and "run" successfully,
  # reporting a green run that measured nothing.
  profiling_error "profiler '$PROFILER_NAME' declared an empty command"
  exit 78
fi
printf '%s\0' "${cmd[@]}" > "$PROFILING_COMMAND_FILE"

if [ "${PROFILING_RESOLVE_ONLY:-0}" = "1" ]; then
  exit 0
fi

cd "$PACKAGE_WORKDIR" || {
  profiling_error "PACKAGE_WORKDIR does not exist: $PACKAGE_WORKDIR"
  exit 77
}

# Installed only after the cd succeeded: a post_run doing relative cleanup
# must never fire from some other directory.
__profiling_post_run() {
  if declare -F package_post_run >/dev/null; then
    package_post_run || profiling_warn "package_post_run exited $?"
  fi
}
trap __profiling_post_run EXIT

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
