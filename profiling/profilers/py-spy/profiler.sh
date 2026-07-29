# py-spy: a sampling profiler that reads another process's memory.
#
# It has one behaviour the harness has to work around: `py-spy record -- <cmd>`
# exits 0 even when <cmd> failed.  Since profiler_wrap's exit status *is* the
# workload's exit status, using that form would silently record a broken
# workload as a successful run.
#
# So by default this profiler starts the workload itself and attaches py-spy to
# the resulting pid.  The shell then owns the process, `wait` yields its exact
# exit code, and py-spy stops on its own when the process ends.  The cost is
# that sampling begins a few milliseconds late, so the very start of
# interpreter startup can be missed.
#
# The obvious alternative -- keeping launch mode and wrapping the workload in a
# shell that records $? -- was measured and rejected: the extra fork makes
# py-spy miss the Python process entirely on sub-second workloads (roughly half
# of runs), which is a far worse trade than a few lost milliseconds.
#
# Set PYSPY_CAPTURE_EXIT_CODE=0 to get plain `py-spy record -- <cmd>` launch
# mode back, at the cost of failed runs being reported as "ok".

_pyspy_capture_enabled() {
    [ "${PYSPY_CAPTURE_EXIT_CODE:-1}" = "1" ]
}

# Resolve the output format, honouring the global flamegraph kill switch by
# degrading to a cheaper format rather than failing the run.
_pyspy_resolve_format() {
    format="${PYSPY_FORMAT:-flamegraph}"
    ext="${PYSPY_EXT:-svg}"
    if ! profiling_flamegraphs_enabled && [ "$format" = "flamegraph" ]; then
        format="${PYSPY_FALLBACK_FORMAT:-speedscope}"
        ext="${PYSPY_FALLBACK_EXT:-json}"
    fi
}

# Builds `cmd`.  With a pid argument it is the attach-mode command; without
# one it is launch mode, which appends the workload itself.
_pyspy_build_cmd() {
    local pid="${1:-}"
    local format ext
    _pyspy_resolve_format

    cmd=(py-spy record --rate "${PYSPY_RATE:-100}" --format "$format")
    [ "${PYSPY_SUBPROCESSES:-0}" = "1" ] && cmd+=(--subprocesses)
    [ "${PYSPY_NATIVE:-0}" = "1" ] && cmd+=(--native)
    cmd+=(--output "$(run_artifact "profile.$ext")")

    if [ -n "$pid" ]; then
        cmd+=(--pid "$pid")
    else
        cmd+=(-- "${TARGET_ARGV[@]}")
    fi
}

_pyspy_ptrace_hint() {
    if [ -r /proc/sys/kernel/yama/ptrace_scope ] &&
        [ "$(cat /proc/sys/kernel/yama/ptrace_scope)" != "0" ]; then
        profiling_warn \
            "py-spy failed and /proc/sys/kernel/yama/ptrace_scope is not 0." \
            "In Docker, run with --cap-add=SYS_PTRACE; on a host, run" \
            "'sudo sysctl -w kernel.yama.ptrace_scope=0'. See README.md."
    fi
}

profiler_wrap() {
    require_bin py-spy
    local cmd status spy_status target_pid spy_pid

    if ! _pyspy_capture_enabled; then
        # Launch mode: accurate from the first instruction, but py-spy's exit
        # status is all we get, and it does not reflect the workload's.
        _pyspy_build_cmd
        "${cmd[@]}"
        status=$?
        [ "$status" -ne 0 ] && _pyspy_ptrace_hint
        return "$status"
    fi

    # Attach mode.  The workload inherits our stdout/stderr, so its output is
    # still tee'd to the run directory by the harness.
    "${TARGET_ARGV[@]}" &
    target_pid=$!

    _pyspy_build_cmd "$target_pid"
    "${cmd[@]}" &
    spy_pid=$!

    wait "$target_pid"
    status=$?

    wait "$spy_pid"
    spy_status=$?

    if [ "$spy_status" -ne 0 ]; then
        _pyspy_ptrace_hint
        if [ "$status" -eq 0 ]; then
            # The workload was fine but we have no profile: that is a failed
            # profiling run, not a successful one.
            profiling_error "py-spy exited $spy_status; no profile was recorded"
            return "$spy_status"
        fi
        profiling_warn "py-spy exited $spy_status (workload also failed: $status)"
    fi

    return "$status"
}

profiler_dry_run() {
    local cmd
    if _pyspy_capture_enabled; then
        # The real pid is only known once the workload has started.
        _pyspy_build_cmd '<workload-pid>'
    else
        _pyspy_build_cmd
    fi
    profiling_show_command "${cmd[@]}"
}
