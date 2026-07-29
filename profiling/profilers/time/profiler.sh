# GNU time: a prefix wrapper, so it takes the plain command as-is.

profiler_command() {
    # TIME_FLAGS is intentionally unquoted: it is a flag list, not one argument.
    # shellcheck disable=SC2206
    cmd=(
        "${TIME_BIN:-/usr/bin/time}" ${TIME_FLAGS}
        -o "$(run_artifact "${TIME_OUTPUT:-time.txt}")"
        -- "${TARGET_ARGV[@]}"
    )
}
