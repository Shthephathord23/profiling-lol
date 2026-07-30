# py-spy: a sampling profiler that reads another process's memory.
#
# The command is this profiler's own attach.sh rather than py-spy itself, because py-spy's
# exit status says nothing about the workload it ran and recovering the real one
# takes two processes and a wait -- which is a program, not a command. That
# script carries the full reasoning and the measurements behind it.

profiler_command() {
    require_python_target

    cmd=(
        "$PROFILER_DIR/attach.sh"
        --rate "${PYSPY_RATE:-100}"
        --format "${PYSPY_FORMAT:-flamegraph}"
        --output "$(run_artifact "profile.${PYSPY_EXT:-svg}")"
        --capture-exit-code "${PYSPY_CAPTURE_EXIT_CODE:-1}"
    )
    [ "${PYSPY_SUBPROCESSES:-0}" = "1" ] && cmd+=(--subprocesses)
    [ "${PYSPY_NATIVE:-0}" = "1" ] && cmd+=(--native)
    cmd+=(-- "${TARGET_ARGV[@]}")
}
