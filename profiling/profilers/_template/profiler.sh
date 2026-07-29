# shellcheck shell=bash
# Template for a new profiler's hooks.
#
# A profiler declares a *command*.  The harness resolves that command once and
# uses the same one for --dry-run and for the real run, so what --dry-run
# prints is by construction what executes -- they cannot drift.
#
# Sourced with lib/common.sh already loaded, so these helpers are available:
#   run_artifact <name>          -> absolute path inside RUN_DIR
#   require_python_target        -> abort unless the target is a Python program
#   profiling_flamegraphs_enabled-> honour the global flamegraph kill switch
#   profiling_warn / profiling_error / profiling_die
#
# The target contract (see README.md "The target contract"):
#   TARGET_ARGV[]        the complete plain command, interpreter included.
#                        Use this for *prefix wrappers* (time).
#   TARGET_PYTHON_ARGV[] the same minus the interpreter: `-m module args...`.
#                        Use this for *interpreter replacements* (viztracer,
#                        kernprof).  Unset when PACKAGE_KIND=exec, so call
#                        require_python_target first.
#   TARGET_PYTHON        the interpreter path (unset for PACKAGE_KIND=exec).
#   TARGET_KIND          the package's PACKAGE_KIND.
#
# Also exported: RUN_DIR RUN_ID PACKAGE_NAME PROFILER_NAME PROFILING_ROOT
#                REPO_ROOT PACKAGE_WORKDIR PACKAGE_DIR PROFILER_DIR
#
# PROFILER_DIR is this directory, so helper scripts can live beside this file.

# REQUIRED.  Populate `cmd` with the command to run.  Do not run anything here.
#
# The command is one argv.  If your profiler needs two processes, a wait, or
# any sequencing, put that in a small script in THIS directory and invoke it as
# "$PROFILER_DIR/<script>" -- see profilers/py-spy/attach.sh.  It belongs here
# rather than in lib/ because it is this profiler's implementation, not shared
# machinery.  Do not reach for `bash -c '...'`: it technically fits in one argv,
# but it hides a shell script inside a string and makes --dry-run unreadable.
profiler_command() {
    # shellcheck disable=SC2034  # cmd is read by lib/harness.sh
    cmd=(
        mytool
        --output "$(run_artifact "${MYTOOL_OUTPUT:-profile.out}")"
        --rate "${MYTOOL_RATE:-100}"
        --
        "${TARGET_ARGV[@]}"
    )
}

# OPTIONAL.  Post-processing, run only when the command succeeded.  This is the
# place for a second command -- rendering a report, converting a format.
# Prefer warning over failing here: a missing report should not turn a good
# profiling run into a red build.
#profiler_post() {
#    "${TARGET_PYTHON:-python3}" -m mytool.report \
#        "$(run_artifact "${MYTOOL_OUTPUT:-profile.out}")" \
#        > "$(run_artifact report.txt)"
#}
