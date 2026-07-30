#!/usr/bin/env bash
#
# Discovery-driven dependency installer for the profiling harness.
#
# It reads *_APT_PACKAGES and *_PIP_PACKAGES out of every profilers/*/.env and
# packages/*/.env, so adding a profiler directory extends the installer with
# no edit here.
#
#   ./install.sh            install everything discovered
#   ./install.sh --check    verify every PROFILER_REQUIRES_BIN is available
#   ./install.sh --dry-run  print the install commands without running them
#
set -uo pipefail

PROFILING_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODE=install
DRY_RUN=0

usage() {
    sed -n '3,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --check) MODE=check ;;
        --dry-run | -n) DRY_RUN=1 ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "install.sh: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

# ----------------------------------------------------------------- helpers ---

warn() { printf 'WARN: %s\n' "$*" >&2; }
err() { printf 'ERROR: %s\n' "$*" >&2; }

# Same discovery rule as the Python core: a directory with a .env whose name
# does not start with '_'.
each_entry_dir() {
    local kind="$1" dir
    for dir in "$PROFILING_ROOT/$kind"/*/; do
        dir="${dir%/}"
        case "$(basename "$dir")" in _* | '*') continue ;; esac
        [ -f "$dir/.env" ] || continue
        printf '%s\n' "$dir"
    done
}

# Read one variable out of a .env without letting it leak into this shell.
read_var() {
    local env_file="$1" var="$2"
    bash -c '
        set -a
        . "'"$PROFILING_ROOT"'/config.env"
        . "$1"
        set +a
        printf "%s" "${!2-}"
    ' _ "$env_file" "$var" 2>/dev/null
}

# Collect a *_APT_PACKAGES / *_PIP_PACKAGES variable across all entries.
collect() {
    local kind="$1" var="$2" dir
    while IFS= read -r dir; do
        read_var "$dir/.env" "$var"
        printf ' '
    done < <(each_entry_dir "$kind")
}

dedupe() {
    tr ' ' '\n' | sed '/^$/d' | sort -u | tr '\n' ' ' | sed 's/ $//'
}

as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        warn "not root and sudo is unavailable; skipping: $*"
        return 1
    fi
}

run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  [dry-run] %s\n' "$*"
        return 0
    fi
    printf '  + %s\n' "$*"
    "$@"
}

# ------------------------------------------------------------------ check ---

do_check() {
    local dir name bins bin status=0 missing=() rows=()

    while IFS= read -r dir; do
        name="$(basename "$dir")"
        bins="$(read_var "$dir/.env" PROFILER_REQUIRES_BIN)"
        if [ -z "$bins" ]; then
            rows+=("$name|(none declared)|ok")
            continue
        fi
        for bin in $bins; do
            case "$bin" in
                /*) [ -x "$bin" ] && rows+=("$name|$bin|ok") || {
                    rows+=("$name|$bin|MISSING")
                    missing+=("$bin")
                    status=1
                } ;;
                *)
                    if command -v "$bin" >/dev/null 2>&1; then
                        rows+=("$name|$bin|ok")
                    else
                        rows+=("$name|$bin|MISSING")
                        missing+=("$bin")
                        status=1
                    fi
                    ;;
            esac
        done
    done < <(each_entry_dir profilers)

    printf '%-16s  %-24s  %s\n' "PROFILER" "BINARY" "STATUS"
    printf '%-16s  %-24s  %s\n' "----------------" "------------------------" "------"
    local row
    for row in "${rows[@]}"; do
        IFS='|' read -r a b c <<<"$row"
        printf '%-16s  %-24s  %s\n' "$a" "$b" "$c"
    done

    # py-spy reads another process's memory, which needs ptrace permission.
    # In a container that means --cap-add=SYS_PTRACE (or --privileged); on the
    # host it means a relaxed yama scope.  Surfacing it here beats a cryptic
    # "Operation not permitted" in the middle of a CI run.
    if command -v py-spy >/dev/null 2>&1 &&
        [ -r /proc/sys/kernel/yama/ptrace_scope ]; then
        local scope
        scope="$(cat /proc/sys/kernel/yama/ptrace_scope)"
        if [ "$scope" != "0" ]; then
            echo
            warn "py-spy is installed but /proc/sys/kernel/yama/ptrace_scope is $scope."
            warn "py-spy needs ptrace. In Docker, add --cap-add=SYS_PTRACE; on a host,"
            warn "run: sudo sysctl -w kernel.yama.ptrace_scope=0"
        fi
    fi

    if [ "$status" -ne 0 ]; then
        echo
        err "missing ${#missing[@]} required binary/binaries: ${missing[*]}"
        err "run ./install.sh to install them"
    fi
    return "$status"
}

# ---------------------------------------------------------------- install ---

do_install() {
    local apt_packages pip_packages
    apt_packages="$( {
        collect profilers PROFILER_APT_PACKAGES
        collect packages PACKAGE_APT_PACKAGES
    } | dedupe)"
    pip_packages="$( {
        collect profilers PROFILER_PIP_PACKAGES
        collect packages PACKAGE_PIP_PACKAGES
    } | dedupe)"

    echo "Discovered apt packages: ${apt_packages:-(none)}"
    echo "Discovered pip packages: ${pip_packages:-(none)}"
    echo

    if [ -n "$apt_packages" ]; then
        if command -v apt-get >/dev/null 2>&1; then
            echo "Installing apt packages..."
            # Both through `run`, so --dry-run prints them instead of running
            # apt-get update for real.
            if run as_root env DEBIAN_FRONTEND=noninteractive apt-get update -qq; then
                # shellcheck disable=SC2086
                run as_root env DEBIAN_FRONTEND=noninteractive \
                    apt-get install -y --no-install-recommends $apt_packages ||
                    warn "apt-get install failed; continuing"
            else
                warn "apt-get update failed; skipping apt packages"
            fi
        else
            warn "apt-get is unavailable; install these yourself: $apt_packages"
        fi
        echo
    fi

    if [ -n "$pip_packages" ]; then
        echo "Installing pip packages..."
        local pip_flags=(--upgrade)
        # Debian/Ubuntu mark the system interpreter as externally managed
        # (PEP 668).  In a container that is exactly where we want these tools,
        # so opt in explicitly rather than failing.
        if python3 -c 'import sysconfig,os,sys; sys.exit(0 if os.path.exists(os.path.join(sysconfig.get_path("stdlib"),"EXTERNALLY-MANAGED")) else 1)'; then
            [ -n "${VIRTUAL_ENV:-}" ] || pip_flags+=(--break-system-packages)
        fi
        # shellcheck disable=SC2086
        run python3 -m pip install "${pip_flags[@]}" $pip_packages ||
            warn "pip install failed; check the output above"
        echo
    fi

    do_package_inits

    echo "Verifying..."
    do_check
}

# ---------------------------------------------------------- package inits ---

# One call: the harness already knows which packages set PACKAGE_INIT=1.
do_package_inits() {
    echo "Initialising packages..."
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  [dry-run] run_profiling.sh --init\n\n'
        return 0
    fi
    "$PROFILING_ROOT/run_profiling.sh" --init || warn "some package inits failed"
    echo
}

case "$MODE" in
    check) do_check ;;
    install) do_install ;;
esac
