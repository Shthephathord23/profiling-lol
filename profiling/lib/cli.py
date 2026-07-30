#!/usr/bin/env python3
"""Entry point for the profiling harness: argument parsing and dispatch.

Run it through ``run_profiling.sh``; see README.md for the user-facing docs.

Exit codes
    0  all runs succeeded (skips do not affect this)
    1  a run failed, timed out, or could not start (missing binary, failed init)
    2  usage error
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import discovery  # noqa: E402
import envfile  # noqa: E402
import report  # noqa: E402
import retention  # noqa: E402
import runner  # noqa: E402
from discovery import DiscoveryError, Package, Profiler  # noqa: E402
from envfile import EnvFileError  # noqa: E402

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_USAGE = 2

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
            "  run_profiling.sh --package my-tool --profiler time \\\n"
            "      --env-package PACKAGE_ARGS='--iterations 1'\n"
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
        "--env-profiler",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="override a profiler knob for this invocation (repeatable)",
    )
    parser.add_argument(
        "--env-package",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="override a package knob for this invocation (repeatable); "
        "wins over --env-profiler on the same key",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve everything and print what would happen; change nothing",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="run package_init for the selected packages and exit",
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


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_overrides(values: Sequence[str], flag: str) -> Dict[str, str]:
    """Parse ``KEY=VALUE`` strings from one ``--env-*`` flag.

    A later occurrence of the same key wins, matching how a shell assignment
    behaves and how the `.env` layers themselves resolve collisions.
    """
    out: Dict[str, str] = {}
    for raw in values:
        key, sep, value = raw.partition("=")
        if not sep or not _ENV_KEY_RE.match(key):
            raise UsageError(f"{flag} expects KEY=VALUE, got {raw!r}")
        out[key] = value
    return out


@dataclass
class Overrides:
    """The ``--env-*`` layer: topmost, above every `.env` file.  The two flags
    are kept apart only so ``meta.json`` can record provenance; on a collision
    ``--env-package`` wins, matching the `.env` layering."""

    profiler: Dict[str, str]
    package: Dict[str, str]

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "Overrides":
        return cls(
            profiler=parse_overrides(args.env_profiler, "--env-profiler"),
            package=parse_overrides(args.env_package, "--env-package"),
        )

    @property
    def merged(self) -> Dict[str, str]:
        return {**self.profiler, **self.package}

    def as_meta(self) -> Dict[str, Dict[str, str]]:
        out = {}
        if self.profiler:
            out["profiler"] = dict(self.profiler)
        if self.package:
            out["package"] = dict(self.package)
        return out


# --------------------------------------------------------------- listings ---


def cmd_list(
    kind: str, env: Dict[str, str], as_json: bool, overrides: Dict[str, str]
) -> int:
    """--list-packages / --list-profilers: print and exit."""
    profiler_entries = discovery.discover_profilers(PROFILING_ROOT)
    if kind == "packages":
        entries = discovery.discover_packages(PROFILING_ROOT)
        rows = report.packages_listing(
            {
                name: discovery.load_package(CONFIG_ENV, entry, overrides=overrides)
                for name, entry in entries.items()
            },
            default_profilers=(env.get("DEFAULT_PROFILERS") or "").split(),
            all_profilers=sorted(profiler_entries),
        )
        columns = [
            ("NAME", "name"),
            ("KIND", "kind"),
            ("PROFILERS", "profilers"),
            ("DESCRIPTION", "description"),
        ]
    else:
        rows = report.profilers_listing(
            {
                name: discovery.load_profiler(CONFIG_ENV, entry, overrides)
                for name, entry in profiler_entries.items()
            }
        )
        columns = [
            ("NAME", "name"),
            ("KINDS", "kinds"),
            ("FLAMEGRAPH", "flamegraph"),
            ("DESCRIPTION", "description"),
        ]

    print(json.dumps(rows, indent=2) if as_json else report.render_table(rows, columns))
    return EXIT_OK


def cmd_init(args: argparse.Namespace, overrides: Dict[str, str]) -> int:
    """Run package_init for the selected packages (default: all) and exit.

    Deliberately ignores PACKAGE_INIT -- that flag governs whether a *run*
    builds the package; --init is the manual trigger (see README, "Package
    init").  A package without the hook is skipped, not an error.
    """
    entries = discovery.discover_packages(PROFILING_ROOT)
    names = discovery.resolve_selection(args.package, entries, "package") or sorted(
        entries
    )

    exit_code = EXIT_OK
    for name in names:
        if args.dry_run:
            print(f"[dry-run] would run package_init for {name} (if defined)")
            continue
        package = discovery.load_package(CONFIG_ENV, entries[name], overrides=overrides)
        print(f"--> init {name}")
        code = runner.run_package_init(package)
        if code == runner.NO_INIT_HOOK:
            print(f"    no package_init in {name}/package.sh; nothing to do")
        elif code != 0:
            print(f"ERROR: package_init failed for {name}", file=sys.stderr)
            exit_code = EXIT_RUN_FAILED
    return exit_code


# -------------------------------------------------------------- retention ---


def cmd_remove_output(args: argparse.Namespace, env: Dict[str, str]) -> int:
    if args.remove_output not in ("keep", "all"):
        raise UsageError(
            f"--remove-output takes no value or 'all', got {args.remove_output!r}"
        )

    keep = 0 if args.remove_output == "all" else args.keep
    if keep is None:
        raw = (env.get("PROFILING_KEEP_DEFAULT") or "").strip() or "1"
        try:
            keep = int(raw)
        except ValueError as exc:
            raise UsageError(
                f"PROFILING_KEEP_DEFAULT must be an integer, got {raw!r}"
            ) from exc
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

    print(f"Pruning {output_dir} (keep {keep} per package/profiler pair)")
    try:
        removed = retention.prune(
            output_dir, keep, packages or None, profilers or None, args.dry_run
        )
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_RUN_FAILED

    verb = "would remove" if args.dry_run else "removed"
    for path in removed:
        print(f"  {verb} {path}")
    if not removed:
        print("  nothing to remove")
    return EXIT_OK


# -------------------------------------------------------------------- run ---


def _select_profilers_for_package(
    package: Package,
    requested: List[str],
    explicit: bool,
    profiler_entries: Dict[str, discovery.Entry],
    default_profilers: List[str],
) -> List[str]:
    """Resolve the profiler list for one package.

    Names given explicitly on the command line run as given, in command-line
    order, whether or not the package lists them; ``resolve_selection`` has
    already validated those.  ``--profiler all`` falls back through the
    package's own PACKAGE_PROFILERS, then DEFAULT_PROFILERS, then every
    discovered profiler, alphabetically.  The fallback lists come from `.env`
    files, so they are validated here -- blaming the file the name came from.
    """
    if explicit:
        return _dedupe(requested)

    for candidate, source in (
        (package.profilers, f"PACKAGE_PROFILERS of package '{package.name}'"),
        (default_profilers, "DEFAULT_PROFILERS"),
    ):
        if candidate:
            names = sorted(_dedupe(candidate))
            unknown = [n for n in names if n not in profiler_entries]
            if unknown:
                raise UsageError(
                    f"unknown profiler(s) in {source}: "
                    + ", ".join(unknown)
                    + "; available: "
                    + ", ".join(sorted(profiler_entries))
                )
            return names
    return sorted(profiler_entries)


def _dedupe(names: List[str]) -> List[str]:
    seen, out = set(), []
    for name in names:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _missing_binaries(profiler: Profiler, env: Dict[str, str]) -> List[str]:
    missing = []
    for binary in profiler.requires_bin:
        if binary.startswith("/"):
            if not os.access(binary, os.X_OK):
                missing.append(binary)
        elif shutil.which(binary, path=env.get("PATH")) is None:
            missing.append(binary)
    return missing


@dataclass
class RunContext:
    """Everything a single run needs that does not vary between runs."""

    dry_run: bool
    overrides: Overrides
    output_dir: Path
    repo_root: Path


def _run_pair(
    ctx: RunContext,
    package_entry: discovery.Entry,
    profiler_entry: discovery.Entry,
) -> "tuple[Dict, int]":
    """Run one (package, profiler) pair -- and only that pair: both entries
    arrive already resolved and validated by the caller.

    Returns the record for ``summary.json`` and the exit-code category this
    outcome contributes.  Every branch returns both together, so no path can
    record an outcome without also accounting for it in the exit code.
    """
    package_name = package_entry.name
    profiler_name = profiler_entry.name

    profiler = discovery.load_profiler(
        CONFIG_ENV, profiler_entry, ctx.overrides.profiler
    )
    # Full layering: config.env -> profiler -> package -> --env-*.  The
    # package sits above the profiler so it can tune that profiler for itself,
    # which is why this is rebuilt per pair rather than hoisted out of the loop.
    package = discovery.load_package(
        CONFIG_ENV, package_entry, profiler.entry.env_file, ctx.overrides.merged
    )

    # 1. Kind compatibility -- a skip, never a failure.
    if not profiler.supports(package.kind):
        reason = f"kind {package.kind} not supported by {profiler_name}"
        print(f"SKIP {package_name} / {profiler_name}: {reason}")
        return _record_status(package_name, profiler_name, "skipped", reason), EXIT_OK

    # 2. Required binaries.
    missing = _missing_binaries(profiler, package.env)
    if missing:
        reason = "missing required binary: " + ", ".join(missing)
        if ctx.dry_run:
            print(f"WARN: {profiler_name} would fail: {reason}", file=sys.stderr)
        else:
            print(
                f"ERROR {package_name} / {profiler_name}: {reason} (run install.sh)",
                file=sys.stderr,
            )
            record = _record_status(package_name, profiler_name, "failed", reason)
            return record, EXIT_RUN_FAILED

    # 3. Resolve the workload and where its artifacts go.
    try:
        target = runner.build_target(package)
        run_id = runner.make_run_id(package.env)
    except (runner.RunError, DiscoveryError) as exc:
        raise UsageError(str(exc)) from exc

    run_dir = ctx.output_dir / package_name / profiler_name / run_id

    # 4. Dry run: resolve and print, create nothing, start nothing.
    if ctx.dry_run:
        _print_dry_run(package, profiler, target, run_dir, run_id, ctx.overrides)
        return (
            _record_status(package_name, profiler_name, "skipped", "dry run"),
            EXIT_OK,
        )

    # 5-6. Execute, then record.
    print(f"==> {package_name} / {profiler_name} -> {run_dir}")
    if runner.reset_run_dir(run_dir, ctx.output_dir):
        # Only reachable when RUN_ID is pinned and re-run; say so rather than
        # deleting the previous build's artifacts silently.
        print(f"    cleared previous artifacts in {run_id}")

    result = runner.execute(
        package=package,
        profiler=profiler,
        target=target,
        run_dir=run_dir,
        run_id=run_id,
    )
    meta = report.write_meta(
        result, package, profiler, ctx.repo_root, ctx.overrides.as_meta()
    )
    report.update_latest(run_dir)

    print(
        f"    {result.status} in {result.duration_s:.2f}s"
        + (f" ({result.reason})" if result.reason else "")
    )
    failed = result.status in ("failed", "timeout")
    return meta, EXIT_RUN_FAILED if failed else EXIT_OK


def cmd_run(
    args: argparse.Namespace, env: Dict[str, str], overrides: Overrides
) -> int:
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
    # Empty here means 'all' expanded over an empty tree; exiting 0 having run
    # nothing would look like success.
    if not package_names:
        raise UsageError("no packages discovered under packages/")
    if not requested_profilers:
        raise UsageError("no profilers discovered under profilers/")
    # "--profiler all" means "each package's own list"; explicit names always run.
    explicit = not _is_all(args.profiler)

    default_profilers = (env.get("DEFAULT_PROFILERS") or "").split()

    ctx = RunContext(
        dry_run=args.dry_run,
        overrides=overrides,
        output_dir=Path(
            env.get("PROFILING_OUTPUT_DIR") or (PROFILING_ROOT / "output")
        ),
        repo_root=Path(env.get("REPO_ROOT") or PROFILING_ROOT.parent),
    )

    records: List[Dict] = []
    exit_code = EXIT_OK

    try:
        for package_name in package_names:
            package_entry = package_entries[package_name]
            # Loaded without a profiler beneath it, purely to read
            # PACKAGE_PROFILERS: which profilers apply cannot be known until
            # the package has been read.
            base_package = discovery.load_package(
                CONFIG_ENV, package_entry, overrides=ctx.overrides.package
            )

            selected = _select_profilers_for_package(
                base_package,
                requested_profilers,
                explicit,
                profiler_entries,
                default_profilers,
            )

            # PACKAGE_INIT=1: call the hook once, before this package's runs.
            # Set it to 0 and a run never builds the package; --init still can.
            if base_package.init_enabled:
                if args.dry_run:
                    print(f"    [dry-run] would run package_init for {package_name}")
                elif _init_for_run(base_package) != 0:
                    print(
                        f"ERROR: package_init failed for {package_name}; "
                        "skipping its runs",
                        file=sys.stderr,
                    )
                    for profiler_name in selected:
                        records.append(
                            _record_status(
                                package_name,
                                profiler_name,
                                "failed",
                                "package init failed",
                            )
                        )
                    exit_code = max(exit_code, EXIT_RUN_FAILED)
                    continue

            for profiler_name in selected:
                record, category = _run_pair(
                    ctx, package_entry, profiler_entries[profiler_name]
                )
                records.append(record)
                exit_code = max(exit_code, category)
    finally:
        # Written even when a later package aborts the invocation: completed
        # runs are already on disk, and the summary is what indexes them.
        if not args.dry_run and records:
            summary = report.write_summary(
                ctx.output_dir, sys.argv[1:], records, exit_code
            )
            print(f"\nSummary: {summary}")
        _print_totals(records)

    return exit_code


def _init_for_run(package: Package) -> int:
    """package_init as part of a run.  A missing hook is a configuration error
    here -- the .env asked for an init that does not exist."""
    code = runner.run_package_init(package)
    if code == runner.NO_INIT_HOOK:
        print(
            f"ERROR: {package.name}/.env sets PACKAGE_INIT=1 but package.sh "
            "defines no package_init",
            file=sys.stderr,
        )
    return code


def _is_all(values: Sequence[str]) -> bool:
    return any(
        piece.strip() == "all" for value in values for piece in value.split(",")
    )


def _record_status(package: str, profiler: str, status: str, reason: str) -> Dict:
    return {
        "package": package,
        "profiler": profiler,
        "status": status,
        "reason": reason,
        "exit_code": None,
        "run_id": None,
    }


def _print_dry_run(
    package: Package,
    profiler: Profiler,
    target: runner.Target,
    run_dir: Path,
    run_id: str,
    overrides: Overrides,
) -> None:
    """Resolve and print, without creating the run directory or any process."""
    with tempfile.TemporaryDirectory(prefix="profiling-dryrun-") as scratch:
        try:
            argv, command = runner.resolve(
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

    print(f"[dry-run] {package.name} / {profiler.name}")
    print(f"    run dir : {run_dir}")
    print(f"    kind    : {package.kind}")
    print(f"    workdir : {package.workdir}")
    for level, values in overrides.as_meta().items():
        print(
            f"    --env-{level}: "
            + " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(values.items()))
        )
    print("    workload: " + " ".join(shlex.quote(a) for a in argv))
    print("    command : " + " ".join(shlex.quote(a) for a in command))


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
    """Route SIGTERM/SIGHUP through the same path as Ctrl-C, so a cancelled CI
    job kills the workload's session instead of orphaning it."""

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
        # config.env plus the real process environment: the harness's own view
        # of paths and defaults, independent of any package or profiler.
        env = envfile.load_layers(CONFIG_ENV)
        # Parsed before any dispatch, so a malformed --env-* is a usage error
        # whichever action was asked for.
        overrides = Overrides.from_args(args)

        terminal = [
            args.init,
            args.list_packages,
            args.list_profilers,
            args.remove_output is not None,
        ]
        if sum(1 for t in terminal if t) > 1:
            raise UsageError(
                "--init, --list-packages, --list-profilers and --remove-output are "
                "terminal actions and cannot be combined"
            )
        # Checked before dispatch, so a flag that does not apply to the chosen
        # action is an error rather than silently ignored.
        if args.keep is not None and args.remove_output is None:
            raise UsageError("--keep is only meaningful with --remove-output")
        if args.json and not (args.list_packages or args.list_profilers):
            raise UsageError(
                "--json is only meaningful with --list-packages/--list-profilers"
            )
        if args.remove_output is not None and (args.env_package or args.env_profiler):
            raise UsageError(
                "--env-package/--env-profiler do not apply to --remove-output"
            )

        if args.init:
            return cmd_init(args, overrides.package)
        if args.list_packages:
            return cmd_list("packages", env, args.json, overrides.package)
        if args.list_profilers:
            return cmd_list("profilers", env, args.json, overrides.profiler)
        if args.remove_output is not None:
            return cmd_remove_output(args, env)

        return cmd_run(args, env, overrides)

    except (UsageError, DiscoveryError, EnvFileError) as exc:
        # All three mean "the harness was asked to do something it cannot make
        # sense of" -- a bad flag, an unknown name, a .env that will not source.
        # They are one category to a caller, so they share one exit code.
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except retention.RetentionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except runner.RunError as exc:
        # E.g. the reset_run_dir containment check; a clean message beats a
        # traceback whichever internal guard fired.
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_RUN_FAILED
    except BrokenPipeError:
        # stdout went away (e.g. piped into `head`); finish quietly like any
        # pipeline tool instead of tracebacking on interpreter shutdown.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return EXIT_RUN_FAILED
    except OSError as exc:
        # Disk full, permissions, an unwritable output tree: an environment
        # problem, not a bug, so report it as one.
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_RUN_FAILED
    except KeyboardInterrupt:
        # execute() has already killed the in-flight session; this is a
        # backstop for an interrupt that landed between runs.
        runner.terminate_active()
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_RUN_FAILED


if __name__ == "__main__":
    sys.exit(main())
