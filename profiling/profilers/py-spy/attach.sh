#!/usr/bin/env bash
#
# Run a workload under py-spy and exit with the WORKLOAD's status.
#
#   attach.sh [options] -- <workload argv...>
#
# Why this exists: py-spy's own exit status describes py-spy, not the program
# it ran, and the two are uncorrelated.  Measured over ten runs of one command,
# `py-spy record -- <cmd>` returns 0 for a workload that exited 3 and 1 for a
# workload that exited 0 -- the latter whenever its flamegraph renderer finds
# no samples to plot, which depends on how long the workload ran rather than on
# whether it worked.
#
# So by default this starts the workload itself and attaches py-spy to the
# resulting pid.  The shell then owns the process and `wait` yields its exact
# exit code.  The cost is that sampling begins a few milliseconds late, so the
# very start of interpreter startup can be missed.
#
# The obvious alternative -- keeping launch mode and wrapping the workload in a
# shell that records $? -- was measured and rejected: the extra fork makes
# py-spy miss the Python process entirely on short workloads (between 1 in 10
# and 4 in 8 of runs, depending on machine load), which is a far worse trade
# than a few milliseconds.
#
# This lives beside its profiler rather than in lib/ because it is py-spy code,
# not shared code.  It is a separate file rather than part of profiler.sh because
# the harness
# runs a profiler's *resolved command*, and a command is one argv.  Anything
# needing two processes and a wait has to be a program.

set -o pipefail

rate=100
format=flamegraph
output=""
subprocesses=0
native=0
capture=1

die() {
    printf 'py-spy/attach: %s\n' "$*" >&2
    exit 64
}

while [ $# -gt 0 ]; do
    case "$1" in
        --rate) rate="${2:?--rate needs a value}"; shift 2 ;;
        --format) format="${2:?--format needs a value}"; shift 2 ;;
        --output) output="${2:?--output needs a value}"; shift 2 ;;
        --subprocesses) subprocesses=1; shift ;;
        --native) native=1; shift ;;
        # 1 = attach and report the workload's status; 0 = plain launch mode.
        --capture-exit-code) capture="${2:?--capture-exit-code needs a value}"; shift 2 ;;
        --) shift; break ;;
        *) die "unknown option: $1" ;;
    esac
done

[ -n "$output" ] || die "no --output given"
[ $# -gt 0 ] || die "no workload given after --"

spy=(py-spy record --rate "$rate" --format "$format" --output "$output")
[ "$subprocesses" = "1" ] && spy+=(--subprocesses)
[ "$native" = "1" ] && spy+=(--native)

warn() { printf 'WARN: %s\n' "$*" >&2; }

ptrace_hint() {
    if [ -r /proc/sys/kernel/yama/ptrace_scope ] &&
        [ "$(cat /proc/sys/kernel/yama/ptrace_scope)" != "0" ]; then
        warn "py-spy failed and /proc/sys/kernel/yama/ptrace_scope is not 0." \
             "In Docker, run with --cap-add=SYS_PTRACE; on a host, run" \
             "'sudo sysctl -w kernel.yama.ptrace_scope=0'. See README.md."
    fi
}

no_samples_hint() {
    warn "py-spy wrote a profile but collected no samples: the workload" \
         "probably finished faster than the ${rate}Hz sampling interval." \
         "Give it more work, or raise PYSPY_RATE."
}

# True while a pid is a live process.  A finished-but-unreaped child is still a
# pid we can signal, so `kill -0` cannot answer this -- read the state field in
# /proc instead and treat Z (zombie) as finished.  The comm field can contain
# spaces and parentheses, so parse from the last ") ".
pid_running() {
    local line state
    [ -r "/proc/$1/stat" ] || return 1
    line=$(cat "/proc/$1/stat" 2>/dev/null) || return 1
    state=${line##*') '}
    state=${state%% *}
    [ -n "$state" ] && [ "$state" != "Z" ]
}

# ---------------------------------------------------------- launch mode ---

if [ "$capture" != "1" ]; then
    "${spy[@]}" -- "$@"
    spy_status=$?
    if [ "$spy_status" -ne 0 ] && [ ! -f "$output" ]; then
        ptrace_hint
        printf 'ERROR: py-spy exited %s; no profile was recorded\n' "$spy_status" >&2
        exit "$spy_status"
    fi
    [ "$spy_status" -ne 0 ] && no_samples_hint
    warn "--capture-exit-code=0: the workload's own exit status is not" \
         "observable in launch mode and is being reported as 0."
    exit 0
fi

# ---------------------------------------------------------- attach mode ---

# The workload inherits our stdout/stderr, so the harness still tees its output.
"$@" &
target_pid=$!

"${spy[@]}" --pid "$target_pid" &
spy_pid=$!

# Wait on py-spy first, so its exit can be interpreted.  Whether the workload
# was still running at that moment is what separates py-spy's two failure
# modes, and the answer is only observable while it is happening:
#
#   workload still running -> py-spy stopped on its own account (ptrace denied,
#                             unreadable process): a real error.
#   workload already gone  -> py-spy merely outlived its target.  On a short
#                             workload it can miss the attach window entirely;
#                             that costs a profile, not a run.
wait "$spy_pid"
spy_status=$?
if pid_running "$target_pid"; then
    target_was_running=1
else
    target_was_running=0
fi

wait "$target_pid"
status=$?

if [ "$spy_status" -ne 0 ]; then
    if [ -f "$output" ]; then
        no_samples_hint
    elif [ "$target_was_running" -eq 1 ]; then
        ptrace_hint
        if [ "$status" -eq 0 ]; then
            printf 'ERROR: py-spy exited %s; no profile was recorded\n' "$spy_status" >&2
            exit "$spy_status"
        fi
        warn "py-spy exited $spy_status (workload also failed: $status)"
    else
        warn "py-spy exited $spy_status without recording a profile: the" \
             "workload finished before sampling could start. Give it more" \
             "work if you need a profile of this run."
    fi
fi

exit "$status"
