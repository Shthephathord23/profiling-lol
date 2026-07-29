# viztracer: an interpreter *replacement*, not a prefix wrapper.  It is itself
# the Python launcher, so it takes TARGET_PYTHON_ARGV (`-m module args...`),
# never TARGET_ARGV -- `viztracer python -m tool` would trace the wrong thing.

_viztracer_build_cmd() {
    # shellcheck disable=SC2206
    cmd=(
        viztracer
        --output_file "$(run_artifact "${VIZTRACER_OUTPUT:-trace.json}")"
        --max_stack_depth "${VIZTRACER_MAX_DEPTH:-64}"
        --tracer_entries "${VIZTRACER_TRACER_ENTRIES:-200000}"
        ${VIZTRACER_FLAGS}
        "${TARGET_PYTHON_ARGV[@]}"
    )
}

profiler_wrap() {
    require_python_target
    require_bin viztracer
    local cmd
    _viztracer_build_cmd
    "${cmd[@]}"
}

profiler_dry_run() {
    require_python_target
    local cmd
    _viztracer_build_cmd
    profiling_show_command "${cmd[@]}"
}
