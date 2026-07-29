# Template for a new profiler's hooks.
#
# Sourced with lib/common.sh already loaded, so these helpers are available:
#   run_artifact <name>          -> absolute path inside RUN_DIR
#   require_bin <bin>...         -> abort with a clear message if missing
#   require_python_target        -> abort unless the target is a Python program
#   profiling_flamegraphs_enabled-> honour the global flamegraph kill switch
#   profiling_warn / profiling_error / profiling_die
#   profiling_show_command <argv>-> emit an argv for --dry-run
#
# The target contract (see README.md "The target contract"):
#   TARGET_ARGV[]        the complete plain command, interpreter included.
#                        Use this for *prefix wrappers* (time, py-spy).
#   TARGET_PYTHON_ARGV[] the same minus the interpreter: `-m module args...`.
#                        Use this for *interpreter replacements* (viztracer,
#                        kernprof).  Unset when PACKAGE_KIND=exec, so call
#                        require_python_target first.
#   TARGET_PYTHON        the interpreter path (unset for PACKAGE_KIND=exec).
#   TARGET_KIND          the package's PACKAGE_KIND.
#
# Also exported: RUN_DIR RUN_ID PACKAGE_NAME PROFILER_NAME PROFILING_ROOT
#                REPO_ROOT PACKAGE_WORKDIR

# Build the command once and share it between wrap and dry-run, so --dry-run
# can never print something different from what actually runs.
_template_build_cmd() {
    cmd=(
        mytool
        --output "$(run_artifact "${MYTOOL_OUTPUT:-profile.out}")"
        --rate "${MYTOOL_RATE:-100}"
        --
        "${TARGET_ARGV[@]}"
    )
}

# REQUIRED.  Runs the workload under the profiler.  Receives TARGET_ARGV as
# positional arguments as well.  Its exit status *is* the workload's exit
# status -- do not swallow it.
profiler_wrap() {
    require_bin mytool
    local cmd
    _template_build_cmd
    "${cmd[@]}"
}

# OPTIONAL.  Post-processing, run only when profiler_wrap succeeded.
# Prefer warning over failing here: a missing report should not turn a good
# profiling run into a red build.
#profiler_post() {
#    "${TARGET_PYTHON:-python3}" -m mytool.report \
#        "$(run_artifact "${MYTOOL_OUTPUT:-profile.out}")" \
#        > "$(run_artifact report.txt)"
#}

# OPTIONAL but recommended.  Print the exact command --dry-run should show.
profiler_dry_run() {
    local cmd
    _template_build_cmd
    profiling_show_command "${cmd[@]}"
}
