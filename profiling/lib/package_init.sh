#!/usr/bin/env bash
# Run a package's package_init hook.  $1 is the package.sh path; invoked by
# lib/runner.py with the package's layered environment exported.
#
# Exit 79 means there was no hook to call -- keep in sync with NO_INIT_HOOK
# in lib/runner.py.

set -o pipefail
. "$PROFILING_ROOT/lib/common.sh"
# A package without package.sh has no hook -- same outcome as a package.sh
# without the function, minus bash's "cannot open" noise.
[ -f "$1" ] || exit 79
. "$1"
if ! declare -F package_init >/dev/null; then
  exit 79
fi
package_init
