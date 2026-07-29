# line-profiler (kernprof): an interpreter *replacement*, like viztracer.
# It accepts Python's own trailing shape, so TARGET_PYTHON_ARGV drops straight
# in after the flags.

_kernprof_build_cmd() {
    cmd=(kernprof --line-by-line)
    if [ -n "${LINE_PROFILER_TARGETS}" ]; then
        cmd+=(--prof-mod "$LINE_PROFILER_TARGETS")
        [ "${LINE_PROFILER_PREIMPORTS:-0}" = "1" ] && cmd+=(--preimports)
    fi
    cmd+=(
        --outfile "$(run_artifact "${LINE_PROFILER_OUTPUT:-out.lprof}")"
        "${TARGET_PYTHON_ARGV[@]}"
    )
}

profiler_wrap() {
    require_python_target
    require_bin kernprof
    local cmd
    _kernprof_build_cmd
    "${cmd[@]}"
}

# Turn the binary .lprof into the human-readable line table.
profiler_post() {
    local lprof report
    lprof="$(run_artifact "${LINE_PROFILER_OUTPUT:-out.lprof}")"
    report="$(run_artifact "${LINE_PROFILER_REPORT:-report.txt}")"

    if [ ! -s "$lprof" ]; then
        profiling_warn "kernprof wrote no profile data to $lprof"
        return 0
    fi

    # Use the *target* interpreter: it is the one that has line_profiler
    # importable in the same environment kernprof ran under.
    if ! "${TARGET_PYTHON:-python3}" -m line_profiler "$lprof" > "$report"; then
        profiling_warn "could not render $lprof; the binary profile is still available"
        return 0
    fi

    if [ ! -s "$report" ]; then
        profiling_warn \
            "line-profiler produced an empty report. Set LINE_PROFILER_TARGETS in" \
            "packages/$PACKAGE_NAME/.env, or decorate functions with @profile."
    fi
}

profiler_dry_run() {
    require_python_target
    local cmd
    _kernprof_build_cmd
    profiling_show_command "${cmd[@]}"
}
