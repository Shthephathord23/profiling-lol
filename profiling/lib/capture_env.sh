#!/usr/bin/env bash
# Source `.env` layers ("$@", in order) and print the resulting exported
# environment NUL-separated.  Invoked by lib/envfile.py, which parses the
# output; the failure marker below must match _parse there.
#
# The trap is armed until every file has been sourced, so any exit before
# then names the current file -- a failure under set -e, a syntax error that
# kills the shell outright, or a `.env` calling `exit` directly (even
# `exit 0`, which would otherwise return an empty capture that looks like
# success).

__profiling_current=""
trap 'printf "__PROFILING_ENV_FAIL__%s\n" "$__profiling_current" >&2' EXIT
set -e
set -a
for __profiling_current in "$@"; do
  . "$__profiling_current"
done
set +a
set +e
trap - EXIT
env -0
