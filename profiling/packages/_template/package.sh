# shellcheck shell=bash
# Template for a new package's hooks.  Every hook is optional; this file may
# be empty.  All of them run with the merged environment exported and
# cwd = PACKAGE_WORKDIR.
#
# lib/common.sh is already sourced, so run_artifact, require_bin,
# profiling_warn etc. are available here too.

# Build step, called once before this package's runs when PACKAGE_INIT=1 in the
# .env.  Absent or 0 and it is never called.
#
# The harness does no staleness tracking, so this runs on every invocation:
# guard the expensive part yourself.  One line usually does it, and the package
# is the only thing that knows what "already built" means:
#
#package_init() {
#    [ -x .venv/bin/python ] || python3 -m venv .venv
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
