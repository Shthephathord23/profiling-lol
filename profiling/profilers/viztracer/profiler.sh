# viztracer: an interpreter *replacement*, not a prefix wrapper.  It is itself
# the Python launcher, so it takes TARGET_PYTHON_ARGV (`-m module args...`),
# never TARGET_ARGV -- `viztracer python -m tool` would trace the wrong thing.

profiler_command() {
    require_python_target
    # VIZTRACER_FLAGS is intentionally unquoted: it is a flag list.
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
