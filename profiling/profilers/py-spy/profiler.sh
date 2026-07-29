# py-spy: a sampling profiler that reads another process's memory.
#
# It has one behaviour the harness has to work around: py-spy's exit status
# describes *py-spy*, not the program it ran, and the two are uncorrelated.
# Measured over repeated runs of one command, `py-spy record -- <cmd>` returns
# 0 for a workload that exited 3, and 1 for a workload that exited 0 -- the
# latter whenever its flamegraph renderer found no samples to plot ("No stack
# counts found"), which is a property of how long the workload ran, not of
# whether it worked.  Since profiler_wrap's exit status *is* the workload's
# exit status, taking py-spy's status at face value would report broken
# workloads as successful and healthy ones as broken, at random.
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
# mode back, at the cost of the workload's exit status being unknowable.

_pyspy_capture_enabled() {
    [ "${PYSPY_CAPTURE_EXIT_CODE:-1}" = "1" ]
}

# Resolve the output format, honouring the global flamegraph kill switch by
# degrading to a cheaper format rather than failing the run.  Sets `format`
# and `ext`, which the caller must declare local.
_pyspy_resolve_format() {
    format="${PYSPY_FORMAT:-flamegraph}"
    ext="${PYSPY_EXT:-svg}"
    if ! profiling_flamegraphs_enabled && [ "$format" = "flamegraph" ]; then
        format="${PYSPY_FALLBACK_FORMAT:-speedscope}"
        ext="${PYSPY_FALLBACK_EXT:-json}"
    fi
}

_pyspy_output_path() {
    local format ext
    _pyspy_resolve_format
    run_artifact "profile.$ext"
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

# Tell apart py-spy's two very different non-zero exits.  When it could not
# attach at all -- ptrace denied, process already gone -- it writes no output
# file; when it attached but collected nothing, it still writes one (an empty
# flamegraph).  Only the first is a broken profiling run.
#
# Returns 0 when a profile exists.
_pyspy_wrote_profile() {
    [ -f "$(_pyspy_output_path)" ]
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

# True while a pid is a live process.  A finished-but-unreaped child is still
# a pid we can signal, so `kill -0` cannot answer this -- check the state field
# in /proc instead and treat Z (zombie) as finished.  The comm field can hold
# spaces and parentheses, so parse from the last ") ".
_pyspy_pid_running() {
    local line state
    [ -r "/proc/$1/stat" ] || return 1
    line=$(cat "/proc/$1/stat" 2>/dev/null) || return 1
    state=${line##*') '}
    state=${state%% *}
    [ -n "$state" ] && [ "$state" != "Z" ]
}

_pyspy_no_samples_hint() {
    profiling_warn \
        "py-spy wrote a profile but collected no samples: the workload" \
        "probably finished faster than the ${PYSPY_RATE:-100}Hz sampling" \
        "interval. Give it more work, or raise PYSPY_RATE."
}

profiler_wrap() {
    require_bin py-spy
    local cmd status spy_status target_pid spy_pid target_was_running

    if ! _pyspy_capture_enabled; then
        # Launch mode.  py-spy's status says nothing about the workload, so
        # the best available signal is whether a profile came out at all.
        _pyspy_build_cmd
        "${cmd[@]}"
        spy_status=$?
        if [ "$spy_status" -ne 0 ] && ! _pyspy_wrote_profile; then
            _pyspy_ptrace_hint
            profiling_error "py-spy exited $spy_status; no profile was recorded"
            return "$spy_status"
        fi
        [ "$spy_status" -ne 0 ] && _pyspy_no_samples_hint
        profiling_warn \
            "PYSPY_CAPTURE_EXIT_CODE=0: the workload's own exit status is not" \
            "observable in launch mode and is being reported as 0."
        return 0
    fi

    # Attach mode.  The workload inherits our stdout/stderr, so its output is
    # still tee'd to the run directory by the harness.
    "${TARGET_ARGV[@]}" &
    target_pid=$!

    _pyspy_build_cmd "$target_pid"
    "${cmd[@]}" &
    spy_pid=$!

    # Wait on py-spy first, so that its exit can be interpreted.  Whether the
    # workload was still running at that moment is what separates py-spy's two
    # failure modes, and the answer is only observable while it is happening:
    #
    #   workload still running -> py-spy stopped on its own account (ptrace
    #                             denied, unreadable process): a real error.
    #   workload already gone  -> py-spy merely outlived its target.  On a
    #                             short workload it can miss the attach window
    #                             entirely; that costs a profile, not a run.
    wait "$spy_pid"
    spy_status=$?
    if _pyspy_pid_running "$target_pid"; then
        target_was_running=1
    else
        target_was_running=0
    fi

    wait "$target_pid"
    status=$?

    if [ "$spy_status" -ne 0 ]; then
        if _pyspy_wrote_profile; then
            # Attached fine, collected nothing.  Not the workload's fault.
            _pyspy_no_samples_hint
        elif [ "$target_was_running" -eq 1 ]; then
            _pyspy_ptrace_hint
            if [ "$status" -eq 0 ]; then
                # No profile, and py-spy quit while there was still something
                # to profile: a genuinely failed profiling run.
                profiling_error "py-spy exited $spy_status; no profile was recorded"
                return "$spy_status"
            fi
            profiling_warn "py-spy exited $spy_status (workload also failed: $status)"
        else
            # Lost the race against a short-lived workload.  Warning only --
            # failing here would make fast workloads flaky in CI.
            profiling_warn \
                "py-spy exited $spy_status without recording a profile: the" \
                "workload finished before sampling could start. Give it more" \
                "work if you need a profile of this run."
        fi
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
