# Hooks for the example package.

# One-time build: a virtualenv plus byte-compiled sources.
#
# --system-site-packages is deliberate.  viztracer and kernprof run as the
# interpreter they were installed into, and line-profiler's profiler_post
# imports line_profiler from PACKAGE_PYTHON; inheriting the system site
# packages means one set of profiler installs serves both.
#
# Nothing here touches the network, so the example works in an offline
# container.
package_init() {
    local venv="$MYTOOL_HOME/.venv"

    if [ ! -x "$venv/bin/python" ]; then
        echo "creating virtualenv at $venv"
        rm -rf "$venv"
        python3 -m venv --system-site-packages "$venv" || return 1
    fi

    echo "byte-compiling the workload"
    "$venv/bin/python" -m compileall -q "$MYTOOL_HOME/src" || return 1

    "$venv/bin/python" -c 'import my_tool; print("my_tool", my_tool.__version__, "importable")'
}

# Fail with a readable message rather than letting the workload die on a
# missing fixture halfway through a profiler's setup.
package_pre_run() {
    [ -f "$MYTOOL_HOME/data/sample.json" ] ||
        profiling_die "missing fixture: $MYTOOL_HOME/data/sample.json"
}

package_post_run() {
    :
}
