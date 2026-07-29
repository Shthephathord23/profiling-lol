"""Process-group lifecycle and output plumbing.

Nothing here knows about packages or profilers.  It exists because the workload
runs in its own session -- so that a timeout can kill the profiler and
everything it spawned together -- and owning a session means the harness has to
take that group down by hand rather than relying on signal delivery.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

__all__ = ["group_alive", "signal_group", "kill_group", "reap", "tee"]


def group_alive(pgid: int) -> bool:
    """True while any process remains in the group (signal 0 probes it)."""
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def signal_group(pgid: int, sig: int) -> bool:
    try:
        os.killpg(pgid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def kill_group(
    proc: "subprocess.Popen",
    term_grace: float = 2.0,
    kill_grace: float = 5.0,
) -> None:
    """SIGTERM the process group, then SIGKILL whatever is still in it.

    Escalation is driven by whether the *group* is empty, not by whether the
    direct child exited.  Those differ in practice: GNU time sets SIGTERM to
    SIG_IGN, and an ignored disposition survives exec, so `time -- sleep 99`
    leaves a sleep that shrugs off the SIGTERM that killed its parent.  Keying
    on the child alone let that sleep outlive the harness.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return

    signal_group(pgid, signal.SIGTERM)

    # Reap the direct child first.  An un-reaped zombie is still a member of the
    # group, so probing before this would always report the group alive and
    # stall for the full grace period on every kill.
    try:
        proc.wait(timeout=term_grace)
    except subprocess.TimeoutExpired:
        pass

    if group_alive(pgid):
        signal_group(pgid, signal.SIGKILL)
        deadline = time.monotonic() + kill_grace
        while time.monotonic() < deadline and group_alive(pgid):
            time.sleep(0.05)
    reap(proc)


def reap(proc: "subprocess.Popen") -> None:
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def tee(stream, path: Path, console) -> None:
    """Stream to the console and to a log file at the same time."""
    try:
        with path.open("wb") as fh:
            for chunk in iter(lambda: stream.readline(), b""):
                fh.write(chunk)
                fh.flush()
                try:
                    console.write(chunk.decode("utf-8", "replace"))
                    console.flush()
                except (ValueError, OSError):
                    pass
    finally:
        try:
            stream.close()
        except OSError:
            pass
