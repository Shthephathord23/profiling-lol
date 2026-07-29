#!/usr/bin/env bash
#
# Call a package's package_init hook.  Invoked by runner.run_package_init with
# the package.sh to source as $1; not meant to be run by hand.
#
# Exits 79 when the package defines no such hook, which the caller treats as a
# skip rather than a failure.

set -o pipefail
# shellcheck source=common.sh
. "$PROFILING_ROOT/lib/common.sh"
# A package need not have a package.sh at all; one that does must source.
# shellcheck source=/dev/null
if [ -f "$1" ] && ! . "$1"; then
    profiling_error "package.sh failed to source (see the error above): $1"
    exit 78
fi

if ! declare -F package_init >/dev/null; then
    exit 79
fi

package_init
