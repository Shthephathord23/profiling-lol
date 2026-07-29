# Template for a new package's hooks.  Every hook is optional; this file may
# be empty.  All of them run with the merged environment exported and
# cwd = PACKAGE_WORKDIR.
#
# lib/common.sh is already sourced, so run_artifact, require_bin,
# profiling_warn etc. are available here too.

# OPTIONAL.  One-time build, run at most once per invocation and then cached
# against a fingerprint of this package's .env, package.sh and every path in
# PACKAGE_INIT_FINGERPRINT.  Delete .state/<package>/ to force a rebuild, or
# pass --force-init.
#
# Output goes to $PROFILING_STATE_DIR/<package>/init.log -- never into a run
# directory, so pruning output can never trigger a rebuild.
#
# If this hook is absent, init is skipped entirely.
#package_init() {
#    uv sync --frozen
#}

# OPTIONAL.  Runs before each profiled run.  Use it to reset caches or
# generate fixtures so runs are comparable.
#package_pre_run() {
#    rm -rf "$PACKAGE_WORKDIR/.cache"
#}

# OPTIONAL.  Runs after each profiled run, including when it failed or timed
# out (it is installed as an EXIT trap).  Keep it quick and idempotent.
#package_post_run() {
#    :
#}

# OPTIONAL escape hatch.  When defined, it overrides the .env-declared entry
# point completely and must populate the arrays itself.  Use it when the
# command cannot be expressed as KIND + ENTRY + ARGS -- a wrapper script, a
# computed argument, a workload chosen by an environment variable.
#
# Populate both arrays when the workload is Python, so that both prefix
# wrappers and interpreter-replacing profilers work:
#package_command() {
#    TARGET_PYTHON="$REPO_ROOT/.venv/bin/python"
#    TARGET_PYTHON_ARGV=(-m my_package.cli --seed "$(date +%s)")
#    TARGET_ARGV=("$TARGET_PYTHON" "${TARGET_PYTHON_ARGV[@]}")
#}
