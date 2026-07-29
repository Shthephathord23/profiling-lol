#!/usr/bin/env python3
"""Entry point for the profiling harness: argument parsing and dispatch.

Run it through ``run_profiling.sh``; see README.md for the user-facing docs.

Exit codes
    0  all runs succeeded (skips do not affect this)
    1  at least one workload run failed or timed out
    2  usage error
    3  a required profiler binary is missing
    4  a package init failed
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import discovery  # noqa: E402
import initstate  # noqa: E402
import report  # noqa: E402
import retention  # noqa: E402
import runner  # noqa: E402
from discovery import DiscoveryError, Package, Profiler  # noqa: E402
from envfile import EnvFileError, apply_real_env, base_environment, source_files  # noqa: E402

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_USAGE = 2
EXIT_MISSING_BIN = 3
EXIT_INIT_FAILED = 4

PROFILING_ROOT = Path(__file__).resolve().parent.parent
CONFIG_ENV = PROFILING_ROOT / "config.env"


class UsageError(Exception):
    """A problem with the command line or with a discovered definition."""


# ------------------------------------------------------------ arg parsing ---


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_profiling.sh",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Run project workloads under a set of profilers.",
        epilog=(
            "Selectors accept a single name, a comma-separated list, repeated\n"
            "flags, or the literal 'all'.\n\n"
            "Examples:\n"
            "  run_profiling.sh --list-packages\n"
            "  run_profiling.sh --package my-tool --profiler all\n"
            "  run_profiling.sh --package all --profiler time,py-spy\n"
            "  run_profiling.sh --remove-output --keep 3\n"
        ),
    )
    parser.add_argument(
        "--package",
        action="append",
        default=[],
        metavar="SEL",
        help="package(s) to run: name, comma-separated list, repeated, or 'all'",
    )
    parser.add_argument(
        "--profiler",
        action="append",
        default=[],
        metavar="SEL",
        help="profiler(s) to run: name, comma-separated list, repeated, or 'all'",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve everything and print what would happen; change nothing",
    )
    parser.add_argument(
        "--force-init",
        action="store_true",
        help="re-run package init unconditionally, ignoring the stamp",
    )
    parser.add_argument(
        "--list-packages",
        action="store_true",
        help="list discovered packages and exit",
    )
    parser.add_argument(
        "--list-profilers",
        action="store_true",
        help="list discovered profilers and exit",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="with --list-*: emit JSON instead of a table (for CI matrices)",
    )
    parser.add_argument(
        "--remove-output",
        nargs="?",
        const="keep",
        default=None,
        metavar="all",
        help="prune run directories and exit; '=all' wipes them (implies --keep 0)",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=None,
        metavar="N",
        help="with --remove-output: runs to keep per package/profiler pair",
    )
    return parser


# ----------------------------------------------------------- global setup ---


def load_global_env() -> Dict[str, str]:
    """config.env plus the real process environment: the harness's own view of
    paths and defaults, independent of any package or profiler."""
    try:
        sourced = source_files([CONFIG_ENV])
    except EnvFileError as exc:
        raise UsageError(str(exc)) from exc
    return apply_real_env(sourced, base=base_environment())


def _int_env(env: Dict[str, str], key: str, default: int) -> int:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise UsageError(f"{key} must be an integer, got {raw!r}") from exc


# --------------------------------------------------------------- listings ---


def cmd_list_packages(env: Dict[str, str], as_json: bool) -> int:
    entries = discovery.discover_packages(PROFILING_ROOT)
    profiler_entries = discovery.discover_profilers(PROFILING_ROOT)
    packages = {
        name: discovery.load_package(CONFIG_ENV, entry)
        for name, entry in entries.items()
    }
    rows = report.packages_listing(
        packages,
        default_profilers=(env.get("DEFAULT_PROFILERS") or "").split(),
        all_profilers=sorted(profiler_entries),
    )
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        print(
            report.render_table(
                rows,
                [
                    ("NAME", "name"),
                    ("KIND", "kind"),
                    ("PROFILERS", "profilers"),
                    ("DESCRIPTION", "description"),
                ],
            )
        )
    return EXIT_OK


def cmd_list_profilers(env: Dict[str, str], as_json: bool) -> int:
    entries = discovery.discover_profilers(PROFILING_ROOT)
    profilers = {
        name: discovery.load_profiler(CONFIG_ENV, entry)
        for name, entry in entries.items()
    }
    rows = report.profilers_listing(profilers)
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        print(
            report.render_table(
                rows,
                [
                    ("NAME", "name"),
                    ("KINDS", "kinds"),
                    ("FLAMEGRAPH", "flamegraph"),
                    ("DESCRIPTION", "description"),
                ],
            )
        )
    return EXIT_OK


# -------------------------------------------------------------- retention ---


def cmd_remove_output(args: argparse.Namespace, env: Dict[str, str]) -> int:
    if args.remove_output not in ("keep", "all"):
        raise UsageError(
            f"--remove-output takes no value or 'all', got {args.remove_output!r}"
        )

    keep = 0 if args.remove_output == "all" else args.keep
    if keep is None:
        keep = _int_env(env, "PROFILING_KEEP_DEFAULT", 1)
    if keep < 0:
        raise UsageError(f"--keep must be >= 0, got {keep}")
    if args.remove_output == "all" and args.keep not in (None, 0):
        raise UsageError("--remove-output=all conflicts with --keep; it implies --keep 0")

    try:
        output_dir = retention.check_output_dir(env.get("PROFILING_OUTPUT_DIR"))
    except retention.RetentionError as exc:
        raise UsageError(str(exc)) from exc

    package_entries = discovery.discover_packages(PROFILING_ROOT)
    profiler_entries = discovery.discover_profilers(PROFILING_ROOT)
    packages = discovery.resolve_selection(args.package, package_entries, "package")
    profilers = discovery.resolve_selection(args.profiler, profiler_entries, "profiler")

    plan = retention.plan_prune(
        output_dir,
        keep=keep,
        packages=packages or None,
        profilers=profilers or None,
    )

    scope = []
    if packages:
        scope.append("packages=" + ",".join(packages))
    if profilers:
        scope.append("profilers=" + ",".join(profilers))
    print(
        f"Pruning {output_dir} (keep {keep} per package/profiler pair"
        + (f"; {'; '.join(scope)}" if scope else "")
        + ")"
    )

    if not plan.remove:
        print("  nothing to remove")
    for path in plan.remove:
        print(f"  {'would remove' if args.dry_run else 'removed'} {path}")

    if plan.skipped:
        print(
            "  left untouched (not run-id shaped): "
            + ", ".join(str(p) for p in plan.skipped)
        )

    if args.dry_run:
        for link in plan.relink:
            print(f"  would re-point {link}")
        print(f"  keeping {len(plan.kept)} run(s)")
        return EXIT_OK

    try:
        retention.apply_plan(plan, output_dir)
    except (retention.RetentionError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_RUN_FAILED

    print(f"  kept {len(plan.kept)} run(s)")
    return EXIT_OK


# -------------------------------------------------------------------- run ---


def _select_profilers_for_package(
    args: argparse.Namespace,
    package: Package,
    requested: List[str],
    explicit: bool,
    all_profilers: List[str],
    default_profilers: List[str],
) -> "tuple[List[str], bool]":
    """Resolve the profiler list for one package (§4).

    Returns (names, forced).  ``forced`` marks profilers named explicitly on
    the command line, which run even when the package does not list them.
    """
    if explicit:
        return requested, True
    declared = package.profilers
    if declared:
        return declared, False
    if default_profilers:
        return default_profilers, False
    return all_profilers, False


def _missing_binaries(profiler: Profiler, env: Dict[str, str]) -> List[str]:
    missing = []
    for binary in profiler.requires_bin:
        if binary.startswith("/"):
            if not os.access(binary, os.X_OK):
                missing.append(binary)
        elif shutil.which(binary, path=env.get("PATH")) is None:
            missing.append(binary)
    return missing


def _warn_unset_knobs(profiler: Profiler, package: Package, env: Dict[str, str]) -> None:
    """Honour a profiler's ``PROFILER_WARN_IF_UNSET`` declaration.

    Data-driven on purpose: line-profiler is the one profiler that is not
    zero-config, but the core must not know its name.
    """
    names = (profiler.env.get("PROFILER_WARN_IF_UNSET") or "").split()
    for name in names:
        if (env.get(name) or "").strip():
            continue
        message = (profiler.env.get("PROFILER_WARN_MESSAGE") or "").strip()
        print(
            f"WARN: {profiler.name} selected for {package.name} but {name} is unset"
            + (f"; {message}" if message else ""),
            file=sys.stderr,
        )


def cmd_run(args: argparse.Namespace, env: Dict[str, str]) -> int:
    package_entries = discovery.discover_packages(PROFILING_ROOT)
    profiler_entries = discovery.discover_profilers(PROFILING_ROOT)

    if not args.package:
        raise UsageError(
            "nothing to do: pass --package <name> (or --package all), or one of "
            "--list-packages / --list-profilers / --remove-output"
        )
    if not args.profiler:
        raise UsageError(
            "--profiler is required for a run; there is no implicit default sweep. "
            "Use --profiler all to run every profiler a package lists, or name them "
            "explicitly (e.g. --profiler time,py-spy)."
        )

    package_names = discovery.resolve_selection(
        args.package, package_entries, "package"
    )
    requested_profilers = discovery.resolve_selection(
        args.profiler, profiler_entries, "profiler"
    )
    # "--profiler all" means "each package's own list"; explicit names are forced.
    explicit = not _is_all(args.profiler)

    all_profilers = sorted(profiler_entries)
    default_profilers = (env.get("DEFAULT_PROFILERS") or "").split()
    output_dir = Path(env.get("PROFILING_OUTPUT_DIR") or (PROFILING_ROOT / "output"))
    state_dir = Path(env.get("PROFILING_STATE_DIR") or (PROFILING_ROOT / ".state"))
    repo_root = Path(env.get("REPO_ROOT") or PROFILING_ROOT.parent)

    records: List[Dict] = []
    exit_code = EXIT_OK
    init_done: Dict[str, bool] = {}

    for package_name in package_names:
        package_entry = package_entries[package_name]
        base_package = discovery.load_package(CONFIG_ENV, package_entry)

        selected, forced = _select_profilers_for_package(
            args,
            base_package,
            requested_profilers,
            explicit,
            all_profilers,
            default_profilers,
        )

        for profiler_name in selected:
            if profiler_name not in profiler_entries:
                raise UsageError(
                    f"package '{package_name}' lists unknown profiler "
                    f"{profiler_name!r} in PACKAGE_PROFILERS; available: "
                    + ", ".join(all_profilers)
                )
            profiler = discovery.load_profiler(
                CONFIG_ENV, profiler_entries[profiler_name]
            )
            # Full layering: config.env -> profiler -> package -> real env.
            package = discovery.load_package(
                CONFIG_ENV, package_entry, profiler.entry.env_file
            )

            is_forced = forced and profiler_name not in base_package.profilers
            if is_forced:
                print(
                    f"WARN: {profiler_name} not listed in PACKAGE_PROFILERS for "
                    f"{package_name}; forced by --profiler",
                    file=sys.stderr,
                )

            # 1. Kind compatibility -- a skip, never a failure.
            if not profiler.supports(package.kind):
                reason = (
                    f"kind {package.kind} not supported by {profiler_name}"
                )
                print(f"SKIP {package_name} / {profiler_name}: {reason}")
                records.append(
                    _skip_record(package_name, profiler_name, reason, is_forced)
                )
                continue

            _warn_unset_knobs(profiler, package, package.env)

            # 2. Required binaries.
            missing = _missing_binaries(profiler, package.env)
            if missing and not args.dry_run:
                reason = "missing required binary: " + ", ".join(missing)
                print(
                    f"ERROR {package_name} / {profiler_name}: {reason} "
                    "(run install.sh)",
                    file=sys.stderr,
                )
                records.append(
                    {
                        "package": package_name,
                        "profiler": profiler_name,
                        "status": "failed",
                        "reason": reason,
                        "forced": is_forced,
                        "exit_code": None,
                    }
                )
                exit_code = max(exit_code, EXIT_MISSING_BIN)
                continue
            if missing and args.dry_run:
                print(
                    f"WARN: {profiler_name} would fail: missing "
                    + ", ".join(missing),
                    file=sys.stderr,
                )

            # 3. Package init, at most once per package per invocation.
            if package_name not in init_done:
                init_done[package_name] = _maybe_init(
                    base_package, state_dir, args, records
                )
            if not init_done[package_name]:
                reason = "package init failed"
                records.append(
                    _record_status(package_name, profiler_name, "failed", reason)
                )
                exit_code = max(exit_code, EXIT_INIT_FAILED)
                continue

            # 4-8. Build the target and run it.
            try:
                target = runner.build_target(package)
                run_id = runner.make_run_id(package.env)
            except (runner.RunError, DiscoveryError) as exc:
                raise UsageError(str(exc)) from exc

            run_dir = output_dir / package_name / profiler_name / run_id

            if args.dry_run:
                _print_dry_run(package, profiler, target, run_dir, run_id, is_forced)
                records.append(
                    _record_status(
                        package_name, profiler_name, "skipped", "dry run"
                    )
                )
                continue

            print(f"==> {package_name} / {profiler_name} -> {run_dir}")
            if runner.reset_run_dir(run_dir, output_dir):
                # Only happens when RUN_ID is pinned and re-run; say so rather
                # than deleting the previous build's artifacts silently.
                print(f"    cleared previous artifacts in {run_id}")
            result = runner.execute(
                env=package.env,
                package=package,
                profiler=profiler,
                target=target,
                run_dir=run_dir,
                run_id=run_id,
                forced=is_forced,
                timeout=package.timeout,
            )
            meta = report.write_meta(result, package, profiler, repo_root)
            report.update_latest(run_dir)
            records.append(meta)

            if result.status in ("failed", "timeout"):
                exit_code = max(exit_code, EXIT_RUN_FAILED)
            print(
                f"    {result.status} in {result.duration_s:.2f}s"
                + (f" ({result.reason})" if result.reason else "")
            )

    if not args.dry_run:
        summary = report.write_summary(output_dir, sys.argv[1:], records, exit_code)
        print(f"\nSummary: {summary}")
    _print_totals(records)
    return exit_code


def _is_all(values: Sequence[str]) -> bool:
    return any(
        piece.strip() == "all" for value in values for piece in value.split(",")
    )


def _skip_record(package: str, profiler: str, reason: str, forced: bool) -> Dict:
    record = _record_status(package, profiler, "skipped", reason)
    record["forced"] = forced
    return record


def _record_status(package: str, profiler: str, status: str, reason: str) -> Dict:
    return {
        "package": package,
        "profiler": profiler,
        "status": status,
        "reason": reason,
        "exit_code": None,
        "run_id": None,
    }


def _maybe_init(
    package: Package,
    state_dir: Path,
    args: argparse.Namespace,
    records: List[Dict],
) -> bool:
    """Run package init if needed.  Returns False when it failed."""
    try:
        needed, reason, fingerprint = initstate.needs_init(
            package, package.env, state_dir, args.force_init
        )
    except initstate.InitError as exc:
        raise UsageError(str(exc)) from exc

    if not needed:
        return True

    if args.dry_run:
        print(f"    [dry-run] would run package_init for {package.name} ({reason})")
        return True

    print(f"--> init {package.name} ({reason})")
    result = initstate.run_init(package, package.env, state_dir, fingerprint or "")
    if result.ok:
        print(f"    init ok in {result.duration_s:.2f}s")
        return True

    print(
        f"ERROR: init failed for {package.name}: {result.reason} "
        f"(log: {result.log_path})",
        file=sys.stderr,
    )
    return False


def _print_dry_run(
    package: Package,
    profiler: Profiler,
    target: runner.Target,
    run_dir: Path,
    run_id: str,
    forced: bool,
) -> None:
    """Resolve and print, without creating the run directory or any process."""
    import shlex

    with tempfile.TemporaryDirectory(prefix="profiling-dryrun-") as scratch:
        try:
            argv = runner.resolve_argv(
                package.env,
                package,
                profiler,
                target,
                Path(scratch),
                run_dir,
                run_id,
            )
        except runner.RunError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return
        command = runner.dry_run_command(
            package.env, package, profiler, target, Path(scratch), run_dir, run_id
        )

    print(f"[dry-run] {package.name} / {profiler.name}")
    print(f"    run dir : {run_dir}")
    print(f"    kind    : {package.kind}")
    print(f"    workdir : {package.workdir}")
    if forced:
        print("    forced  : yes (not in PACKAGE_PROFILERS)")
    print("    workload: " + " ".join(shlex.quote(a) for a in argv))
    if command:
        print("    command : " + " ".join(shlex.quote(a) for a in command))
    else:
        print(
            f"    command : (profilers/{profiler.name}/profiler.sh :: profiler_wrap "
            "-- define profiler_dry_run to show the exact command)"
        )


def _print_totals(records: Sequence[Dict]) -> None:
    counts: Dict[str, int] = {}
    for record in records:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    if counts:
        print(
            "Totals: "
            + ", ".join(f"{k}={counts[k]}" for k in sorted(counts))
        )


# ------------------------------------------------------------------ main ---


def _install_signal_handlers() -> None:
    """Route SIGTERM through the same path as Ctrl-C.

    SIGINT already raises KeyboardInterrupt, which ``runner.execute`` turns
    into a process-group kill.  SIGTERM has no such default, so a cancelled CI
    job would return from the harness while leaving the profiler and the
    workload running.
    """

    def handler(signum, frame):  # noqa: ARG001 - signal handler signature
        raise KeyboardInterrupt

    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError, AttributeError):
            pass  # not the main thread, or the platform lacks the signal


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _install_signal_handlers()

    try:
        env = load_global_env()

        terminal = [args.list_packages, args.list_profilers, args.remove_output is not None]
        if sum(1 for t in terminal if t) > 1:
            raise UsageError(
                "--list-packages, --list-profilers and --remove-output are terminal "
                "actions and cannot be combined"
            )

        if args.list_packages:
            return cmd_list_packages(env, args.json)
        if args.list_profilers:
            return cmd_list_profilers(env, args.json)
        if args.remove_output is not None:
            return cmd_remove_output(args, env)

        if args.keep is not None:
            raise UsageError("--keep is only meaningful with --remove-output")
        if args.json:
            raise UsageError("--json is only meaningful with --list-packages/--list-profilers")

        return cmd_run(args, env)

    except (UsageError, DiscoveryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except retention.RetentionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        # execute() has already killed the in-flight process group; this is a
        # backstop for an interrupt that landed between runs.
        runner.terminate_active()
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_RUN_FAILED


if __name__ == "__main__":
    sys.exit(main())
