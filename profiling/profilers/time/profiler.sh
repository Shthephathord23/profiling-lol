# GNU time: a prefix wrapper, so it consumes TARGET_ARGV as-is.

# Builds the command into the `cmd` array, shared by wrap and dry-run so the
# two can never drift apart.
_time_build_cmd() {
    # TIME_FLAGS is intentionally unquoted: it is a flag list, not one argument.
    # shellcheck disable=SC2206
    cmd=("${TIME_BIN:-/usr/bin/time}" ${TIME_FLAGS} -o "$(run_artifact "${TIME_OUTPUT:-time.txt}")" -- "${TARGET_ARGV[@]}")
}

profiler_wrap() {
    require_bin "${TIME_BIN:-/usr/bin/time}"
    local cmd
    _time_build_cmd
    "${cmd[@]}"
}

profiler_dry_run() {
    local cmd
    _time_build_cmd
    profiling_show_command "${cmd[@]}"
}
