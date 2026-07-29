#!/usr/bin/env python3
"""Tests for the profiling harness.

Run with:  python3 tests/test_harness.py        (stdlib unittest, no deps)
       or:  python3 -m pytest tests/

The harness shells out to bash for env layering and for every run, so most of
these build a throwaway profiling tree in a tmpdir and drive the real CLI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HARNESS_ROOT / "lib"))

import discovery  # noqa: E402
import envfile  # noqa: E402
import retention  # noqa: E402
import runner  # noqa: E402
from discovery import DiscoveryError  # noqa: E402


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), "utf-8")
    return path


class TreeFixture(unittest.TestCase):
    """A self-contained profiling tree: real lib/, synthetic packages+profilers."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="profiling-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.root = self.tmp / "profiling"
        (self.root / "packages").mkdir(parents=True)
        (self.root / "profilers").mkdir(parents=True)
        # A real copy, not a symlink: cli.py derives PROFILING_ROOT from
        # Path(__file__).resolve(), which follows a symlink back to the source
        # tree and would make every run operate on the real packages/.
        shutil.copytree(HARNESS_ROOT / "lib", self.root / "lib")
        shutil.copy(HARNESS_ROOT / "run_profiling.sh", self.root / "run_profiling.sh")
        os.chmod(self.root / "run_profiling.sh", 0o755)

        self.out = self.tmp / "out"
        write(
            self.root / "config.env",
            f"""
            PROFILING_ROOT="{self.root}"
            : "${{REPO_ROOT:={self.tmp}}}"
            : "${{PROFILING_OUT_PATH:={self.tmp}}}"
            : "${{PROFILING_OUTPUT_DIR:={self.out}}}"
            : "${{DEFAULT_PROFILERS:=noop}}"
            : "${{PROFILING_KEEP_DEFAULT:=1}}"
            : "${{PROFILING_FLAMEGRAPHS:=1}}"
            """,
        )

    def add_package(self, name: str, env: str = "", script: str = "") -> Path:
        d = self.root / "packages" / name
        write(d / ".env", env or 'PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\n')
        if script:
            write(d / "package.sh", script)
        return d

    def add_profiler(self, name: str, env: str = "", script: str = "") -> Path:
        d = self.root / "profilers" / name
        write(d / ".env", env or 'PROFILER_KINDS="python-module python-script exec"\n')
        write(
            d / "profiler.sh",
            script or 'profiler_command() { cmd=("${TARGET_ARGV[@]}"); }\n',
        )
        return d

    def run_cli(self, *argv: str, env_extra=None):
        env = dict(os.environ)
        env.pop("RUN_ID", None)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, str(self.root / "lib" / "cli.py"), *argv],
            cwd=self.root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )


# ----------------------------------------------------------------- envfile ---


class TestEnvFile(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="envfile-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_layers_apply_in_order(self):
        a = write(self.tmp / "a.env", 'FOO=first\nONLY_A=yes\n')
        b = write(self.tmp / "b.env", 'FOO=second\n')
        env = envfile.load_layers(a, b, real_env={})
        self.assertEqual(env["FOO"], "second")
        self.assertEqual(env["ONLY_A"], "yes")

    def test_real_env_beats_plain_assignment(self):
        a = write(self.tmp / "a.env", 'PACKAGE_ARGS="--from-file"\n')
        env = envfile.load_layers(a, real_env={"PACKAGE_ARGS": "--from-cli"})
        self.assertEqual(env["PACKAGE_ARGS"], "--from-cli")

    def test_default_idiom_lets_real_env_win(self):
        a = write(self.tmp / "a.env", ': "${KNOB:=fallback}"\n')
        self.assertEqual(envfile.load_layers(a, real_env={})["KNOB"], "fallback")
        got = envfile.load_layers(a, real_env={"KNOB": "override"})
        self.assertEqual(got["KNOB"], "override")

    def test_interpolation_and_command_substitution(self):
        a = write(self.tmp / "a.env", 'BASE=/opt\nDERIVED="$BASE/bin"\nSUB="$(echo hi)"\n')
        env = envfile.load_layers(a, real_env={})
        self.assertEqual(env["DERIVED"], "/opt/bin")
        self.assertEqual(env["SUB"], "hi")

    def test_pathlike_extension_survives_real_env_overlay(self):
        a = write(self.tmp / "a.env", 'PYTHONPATH="/mysrc${PYTHONPATH:+:$PYTHONPATH}"\n')
        env = envfile.load_layers(a, real_env={"PYTHONPATH": "/caller"})
        self.assertEqual(env["PYTHONPATH"], "/mysrc:/caller")

    def test_pathlike_untouched_by_layer_takes_real_env(self):
        a = write(self.tmp / "a.env", 'UNRELATED=1\n')
        env = envfile.load_layers(a, real_env={"PYTHONPATH": "/caller"})
        self.assertEqual(env["PYTHONPATH"], "/caller")

    def test_syntax_error_names_the_offending_file(self):
        good = write(self.tmp / "good.env", 'A=1\n')
        bad = write(self.tmp / "bad.env", 'A="unterminated\n')
        with self.assertRaises(envfile.EnvFileError) as ctx:
            envfile.load_layers(good, bad, real_env={})
        self.assertIn("bad.env", str(ctx.exception))

    def test_nonzero_exit_names_the_offending_file(self):
        bad = write(self.tmp / "boom.env", 'exit 3\n')
        with self.assertRaises(envfile.EnvFileError) as ctx:
            envfile.load_layers(bad, real_env={})
        self.assertIn("boom.env", str(ctx.exception))

    def test_missing_file_is_an_error(self):
        with self.assertRaises(envfile.EnvFileError):
            envfile.load_layers(self.tmp / "nope.env", real_env={})


# --------------------------------------------------------------- discovery ---


class TestSelection(unittest.TestCase):
    def setUp(self):
        self.available = {n: None for n in ("alpha", "beta", "gamma")}

    def test_comma_repeated_and_order(self):
        self.assertEqual(
            discovery.resolve_selection(["gamma,alpha"], self.available, "x"),
            ["gamma", "alpha"],
        )
        self.assertEqual(
            discovery.resolve_selection(["beta", "alpha"], self.available, "x"),
            ["beta", "alpha"],
        )

    def test_all_expands_alphabetically(self):
        self.assertEqual(
            discovery.resolve_selection(["all"], self.available, "x"),
            ["alpha", "beta", "gamma"],
        )

    def test_all_with_explicit_names_is_an_error(self):
        with self.assertRaises(DiscoveryError):
            discovery.resolve_selection(["all,alpha"], self.available, "x")

    def test_duplicates_collapse_keeping_first(self):
        self.assertEqual(
            discovery.resolve_selection(["beta,beta,alpha"], self.available, "x"),
            ["beta", "alpha"],
        )

    def test_unknown_name_lists_what_is_available(self):
        with self.assertRaises(DiscoveryError) as ctx:
            discovery.resolve_selection(["nope"], self.available, "profiler")
        self.assertIn("alpha, beta, gamma", str(ctx.exception))

    def test_empty_selection(self):
        self.assertEqual(discovery.resolve_selection([], self.available, "x"), [])
        self.assertEqual(discovery.resolve_selection(["  "], self.available, "x"), [])


class TestDiscoveryScan(TreeFixture):
    def test_underscore_and_dotdirs_are_hidden(self):
        self.add_package("real")
        self.add_package("_template")
        (self.root / "packages" / ".hidden").mkdir()
        write(self.root / "packages" / ".hidden" / ".env", "X=1\n")
        (self.root / "packages" / "no-env-here").mkdir()
        found = discovery.discover_packages(self.root)
        self.assertEqual(sorted(found), ["real"])

    def test_bad_name_is_an_error_not_a_skip(self):
        self.add_package("Bad-Caps")
        with self.assertRaises(DiscoveryError):
            discovery.discover_packages(self.root)

    def test_missing_directory_is_an_error(self):
        with self.assertRaises(DiscoveryError):
            discovery.discover_packages(self.tmp / "nowhere")


class TestTypedAccessors(TreeFixture):
    def _pkg(self, env: str):
        self.add_package("p", env=env)
        entries = discovery.discover_packages(self.root)
        return discovery.load_package(self.root / "config.env", entries["p"])

    def test_args_use_shell_quoting_not_split(self):
        pkg = self._pkg('PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_ARGS=\'--msg "two words" -n 3\'\n')
        self.assertEqual(pkg.args, ["--msg", "two words", "-n", "3"])

    def test_timeout_parsing(self):
        base = "PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\n"
        self.assertIsNone(self._pkg(base).timeout)
        self.assertIsNone(self._pkg(base + "PACKAGE_TIMEOUT=0\n").timeout)
        self.assertEqual(self._pkg(base + "PACKAGE_TIMEOUT=1.5\n").timeout, 1.5)
        with self.assertRaises(DiscoveryError):
            self._pkg(base + "PACKAGE_TIMEOUT=soon\n").timeout

    def test_init_enabled_is_strict(self):
        base = "PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\n"
        self.assertFalse(self._pkg(base).init_enabled)
        self.assertTrue(self._pkg(base + "PACKAGE_INIT=1\n").init_enabled)
        self.assertFalse(self._pkg(base + "PACKAGE_INIT=0\n").init_enabled)


# ------------------------------------------------------------------ runner ---


class TestRunId(unittest.TestCase):
    def test_generated_id_shape(self):
        rid = runner.make_run_id({})
        self.assertRegex(rid, r"^\d{8}-\d{6}-[0-9a-f]{4}$")

    def test_override_is_sanitized(self):
        self.assertEqual(runner.make_run_id({"RUN_ID": "build-42"}), "build-42")
        self.assertEqual(runner.make_run_id({"RUN_ID": "a b/c"}), "a-b-c")

    def test_traversal_attempts_collapse_to_one_component(self):
        for hostile in ("../../etc", "/etc/passwd", "a/../b"):
            rid = runner.make_run_id({"RUN_ID": hostile})
            self.assertNotIn("/", rid)
            self.assertNotIn(rid, (".", ".."))

    def test_unusable_override_is_rejected(self):
        for bad in ("..", ".", "///", "..."):
            with self.assertRaises(runner.RunError):
                runner.make_run_id({"RUN_ID": bad})

    def test_override_is_length_capped(self):
        self.assertLessEqual(len(runner.make_run_id({"RUN_ID": "x" * 500})), 128)


class TestBuildTarget(TreeFixture):
    def _pkg(self, env: str):
        self.add_package("p", env=env)
        entries = discovery.discover_packages(self.root)
        return discovery.load_package(self.root / "config.env", entries["p"])

    def test_python_module_exposes_both_shapes(self):
        t = runner.build_target(
            self._pkg("PACKAGE_KIND=python-module\nPACKAGE_ENTRY=my.mod\n"
                      "PACKAGE_ARGS=--fast\nPACKAGE_PYTHON=/usr/bin/python3\n")
        )
        self.assertEqual(t.python_argv, ["-m", "my.mod", "--fast"])
        self.assertEqual(t.argv, ["/usr/bin/python3", "-m", "my.mod", "--fast"])

    def test_exec_kind_has_no_python_argv(self):
        t = runner.build_target(
            self._pkg("PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/echo\nPACKAGE_ARGS=hi\n")
        )
        self.assertIsNone(t.python_argv)
        self.assertIsNone(t.python)
        self.assertEqual(t.argv, ["/bin/echo", "hi"])

    def test_unknown_kind_and_empty_entry_are_errors(self):
        with self.assertRaises(runner.RunError):
            runner.build_target(self._pkg("PACKAGE_KIND=wat\nPACKAGE_ENTRY=x\n"))
        with self.assertRaises(runner.RunError):
            runner.build_target(self._pkg("PACKAGE_KIND=exec\nPACKAGE_ENTRY=\n"))

    def test_render_target_sh_unsets_python_argv_for_exec(self):
        pkg = self._pkg("PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\n")
        entries = discovery.discover_profilers(self.root) if (self.root / "profilers").exists() else {}
        self.add_profiler("noop")
        prof = discovery.load_profiler(
            self.root / "config.env", discovery.discover_profilers(self.root)["noop"]
        )
        rendered = runner.render_target_sh(runner.build_target(pkg), pkg, prof)
        self.assertIn("unset TARGET_PYTHON_ARGV", rendered)
        self.assertIn("unset TARGET_PYTHON", rendered)

    def test_render_target_sh_quotes_hostile_args(self):
        pkg = self._pkg(
            "PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/echo\n"
            "PACKAGE_ARGS='\"; touch /tmp/pwned; #\"'\n"
        )
        self.add_profiler("noop")
        prof = discovery.load_profiler(
            self.root / "config.env", discovery.discover_profilers(self.root)["noop"]
        )
        rendered = runner.render_target_sh(runner.build_target(pkg), pkg, prof)
        probe = self.tmp / "pwned"
        script = f'{rendered}\n[ -e "{probe}" ] && echo BREACH\n'
        out = subprocess.run(["bash", "-c", script], stdout=subprocess.PIPE, text=True)
        self.assertNotIn("BREACH", out.stdout)


class TestResetRunDir(TreeFixture):
    def test_clears_contents_and_reports(self):
        run_dir = self.out / "p" / "prof" / "r1"
        (run_dir / "sub").mkdir(parents=True)
        (run_dir / "sub" / "f").write_text("x")
        (run_dir / "top").write_text("y")
        self.assertTrue(runner.reset_run_dir(run_dir, self.out))
        self.assertEqual(list(run_dir.iterdir()), [])

    def test_empty_and_absent_are_noops(self):
        run_dir = self.out / "p" / "prof" / "empty"
        run_dir.mkdir(parents=True)
        self.assertFalse(runner.reset_run_dir(run_dir, self.out))
        self.assertFalse(runner.reset_run_dir(self.out / "nope", self.out))

    def test_refuses_a_dir_outside_the_output_root(self):
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        (outside / "precious").write_text("keep me")
        with self.assertRaises(runner.RunError):
            runner.reset_run_dir(outside, self.out)
        self.assertTrue((outside / "precious").exists())


# --------------------------------------------------------------- retention ---


class TestCheckOutputDir(unittest.TestCase):
    def test_unset_is_refused(self):
        for bad in (None, "", "   "):
            with self.assertRaises(retention.RetentionError):
                retention.check_output_dir(bad)

    def test_relative_is_refused(self):
        with self.assertRaises(retention.RetentionError):
            retention.check_output_dir("relative/output")

    def test_shallow_is_refused(self):
        with self.assertRaises(retention.RetentionError):
            retention.check_output_dir("/tmp")

    def test_reasonable_path_is_accepted(self):
        self.assertEqual(
            retention.check_output_dir("/var/lib/profiling/output"),
            Path("/var/lib/profiling/output"),
        )


class TestPrune(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="prune-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out = self.tmp / "a" / "b" / "output"

    def make_runs(self, package: str, profiler: str, names):
        pair = self.out / package / profiler
        pair.mkdir(parents=True, exist_ok=True)
        for i, name in enumerate(names):
            d = pair / name
            d.mkdir()
            (d / "meta.json").write_text("{}")
            os.utime(d, (1_600_000_000 + i * 10, 1_600_000_000 + i * 10))
        return pair

    def test_keeps_newest_n_by_mtime_not_name(self):
        pair = self.make_runs("p", "prof", ["build-9", "build-10", "build-11"])
        removed = retention.prune(self.out, keep=1)
        self.assertEqual([d.name for d in removed], ["build-9", "build-10"])
        self.assertTrue((pair / "build-11").is_dir())

    def test_keep_zero_removes_everything_and_drops_latest(self):
        pair = self.make_runs("p", "prof", ["r1", "r2"])
        (pair / "latest").symlink_to("r2", target_is_directory=True)
        retention.prune(self.out, keep=0)
        self.assertEqual([d.name for d in pair.iterdir()], [])

    def test_dry_run_removes_nothing(self):
        pair = self.make_runs("p", "prof", ["r1", "r2"])
        removed = retention.prune(self.out, keep=1, dry_run=True)
        self.assertEqual(len(removed), 1)
        self.assertEqual(sorted(d.name for d in pair.iterdir()), ["r1", "r2"])

    def test_retention_is_per_pair(self):
        self.make_runs("p", "one", ["r1", "r2"])
        self.make_runs("p", "two", ["r1", "r2"])
        removed = retention.prune(self.out, keep=1)
        self.assertEqual(len(removed), 2)

    def test_scoping_by_package_and_profiler(self):
        self.make_runs("keep-me", "prof", ["r1", "r2"])
        self.make_runs("prune-me", "prof", ["r1", "r2"])
        removed = retention.prune(self.out, keep=1, packages=["prune-me"])
        self.assertEqual([d.parent.parent.name for d in removed], ["prune-me"])

    def test_latest_symlink_is_repointed_to_survivor(self):
        pair = self.make_runs("p", "prof", ["r1", "r2", "r3"])
        (pair / "latest").symlink_to("r1", target_is_directory=True)
        retention.prune(self.out, keep=1)
        self.assertEqual(os.readlink(pair / "latest"), "r3")

    def test_latest_symlink_is_never_treated_as_a_run(self):
        pair = self.make_runs("p", "prof", ["r1"])
        (pair / "latest").symlink_to("r1", target_is_directory=True)
        removed = retention.prune(self.out, keep=1)
        self.assertEqual(removed, [])

    def test_absent_tree_is_a_noop(self):
        self.assertEqual(retention.prune(self.tmp / "gone", keep=1), [])


# ------------------------------------------------------------ CLI end-to-end ---


class TestCliUsage(TreeFixture):
    def test_no_package_is_a_usage_error(self):
        self.add_package("p")
        self.add_profiler("noop")
        r = self.run_cli()
        self.assertEqual(r.returncode, 2)
        self.assertIn("nothing to do", r.stderr)

    def test_missing_profiler_is_a_usage_error(self):
        self.add_package("p")
        self.add_profiler("noop")
        r = self.run_cli("--package", "p")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--profiler is required", r.stderr)

    def test_terminal_actions_cannot_combine(self):
        self.add_package("p")
        self.add_profiler("noop")
        r = self.run_cli("--list-packages", "--list-profilers")
        self.assertEqual(r.returncode, 2)
        self.assertIn("terminal actions", r.stderr)

    def test_keep_without_remove_output_is_rejected(self):
        self.add_package("p")
        self.add_profiler("noop")
        r = self.run_cli("--package", "p", "--profiler", "noop", "--keep", "3")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--keep is only meaningful", r.stderr)

    def test_json_without_listing_is_rejected(self):
        self.add_package("p")
        self.add_profiler("noop")
        r = self.run_cli("--package", "p", "--profiler", "noop", "--json")
        self.assertEqual(r.returncode, 2)

    def test_remove_output_all_conflicts_with_keep(self):
        self.add_package("p")
        self.add_profiler("noop")
        r = self.run_cli("--remove-output=all", "--keep", "3")
        self.assertEqual(r.returncode, 2)
        self.assertIn("implies --keep 0", r.stderr)

    def test_list_json_is_machine_readable(self):
        self.add_package("p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="noop"\n')
        self.add_profiler("noop")
        r = self.run_cli("--list-packages", "--json")
        self.assertEqual(r.returncode, 0)
        rows = json.loads(r.stdout)
        self.assertEqual(rows[0]["name"], "p")
        self.assertEqual(rows[0]["profilers"], ["noop"])


class TestCliRun(TreeFixture):
    def _basic_tree(self, package_env: str = "", profiler_script: str = ""):
        self.add_package(
            "p",
            env=package_env
            or 'PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/echo\nPACKAGE_ARGS=hello\nPACKAGE_PROFILERS="noop"\n',
        )
        self.add_profiler("noop", script=profiler_script)

    def _summary(self):
        return json.loads((self.out / "summary.json").read_text())

    def test_successful_run_writes_meta_and_summary(self):
        self._basic_tree()
        r = self.run_cli("--package", "p", "--profiler", "noop")
        self.assertEqual(r.returncode, 0, r.stderr)
        summary = self._summary()
        self.assertEqual(summary["counts"]["ok"], 1)
        run_id = summary["runs"][0]["run_id"]
        meta = json.loads((self.out / "p" / "noop" / run_id / "meta.json").read_text())
        self.assertEqual(meta["status"], "ok")
        self.assertEqual(meta["exit_code"], 0)
        self.assertIn("hello", (self.out / "p" / "noop" / run_id / "stdout.log").read_text())

    def test_latest_symlink_points_at_the_run(self):
        self._basic_tree()
        self.run_cli("--package", "p", "--profiler", "noop")
        run_id = self._summary()["runs"][0]["run_id"]
        self.assertEqual(os.readlink(self.out / "p" / "noop" / "latest"), run_id)

    def test_failing_workload_yields_exit_1(self):
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/false\nPACKAGE_PROFILERS="noop"\n'
        )
        self.add_profiler("noop")
        r = self.run_cli("--package", "p", "--profiler", "noop")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self._summary()["runs"][0]["status"], "failed")

    def test_timeout_is_reported_as_timeout(self):
        self.add_package(
            "p",
            env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/sleep\nPACKAGE_ARGS=30\n'
            'PACKAGE_PROFILERS="noop"\nPACKAGE_TIMEOUT=2\n',
        )
        self.add_profiler("noop")
        r = self.run_cli("--package", "p", "--profiler", "noop")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self._summary()["runs"][0]["status"], "timeout")

    def test_timeout_kills_a_sigterm_ignoring_grandchild(self):
        """GNU time sets SIGTERM to SIG_IGN and that survives exec."""
        if not os.access("/usr/bin/time", os.X_OK):
            self.skipTest("/usr/bin/time not installed")
        self.add_package(
            "p",
            env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/sleep\nPACKAGE_ARGS=60\n'
            'PACKAGE_PROFILERS="t"\nPACKAGE_TIMEOUT=2\n',
        )
        self.add_profiler(
            "t",
            script='profiler_command() { cmd=(/usr/bin/time -v -o "$(run_artifact time.txt)" -- "${TARGET_ARGV[@]}"); }\n',
        )
        r = self.run_cli("--package", "p", "--profiler", "t")
        self.assertEqual(r.returncode, 1)
        leftovers = subprocess.run(
            ["pgrep", "-f", "^/bin/sleep 60$"], stdout=subprocess.PIPE, text=True
        )
        self.assertEqual(leftovers.stdout.strip(), "", "orphaned sleep survived")

    def test_kind_incompatibility_skips_without_failing(self):
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="pyonly"\n'
        )
        self.add_profiler("pyonly", env='PROFILER_KINDS="python-module"\n')
        r = self.run_cli("--package", "p", "--profiler", "all")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self._summary()["runs"][0]["status"], "skipped")

    def test_missing_required_binary_yields_exit_3(self):
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="ghost"\n'
        )
        self.add_profiler(
            "ghost",
            env='PROFILER_KINDS="exec"\nPROFILER_REQUIRES_BIN="definitely-not-installed-xyz"\n',
        )
        r = self.run_cli("--package", "p", "--profiler", "ghost")
        self.assertEqual(r.returncode, 3)

    def test_dry_run_creates_nothing(self):
        self._basic_tree()
        r = self.run_cli("--package", "p", "--profiler", "noop", "--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertIn("[dry-run]", r.stdout)
        self.assertIn("/bin/echo hello", r.stdout)
        self.assertFalse(self.out.exists(), "dry run created the output tree")

    def test_dry_run_reflects_package_command_override(self):
        self.add_package(
            "p",
            env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="noop"\n',
            script='package_command() { TARGET_ARGV=(/bin/echo overridden); }\n',
        )
        self.add_profiler("noop")
        r = self.run_cli("--package", "p", "--profiler", "noop", "--dry-run")
        self.assertIn("overridden", r.stdout)

    def test_forced_profiler_warns_and_is_recorded(self):
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="noop"\n'
        )
        self.add_profiler("noop")
        self.add_profiler("other")
        r = self.run_cli("--package", "p", "--profiler", "other")
        self.assertIn("forced by --profiler", r.stderr)
        self.assertTrue(self._summary()["runs"][0]["forced"])

    def test_profiler_all_uses_the_packages_own_list(self):
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="noop"\n'
        )
        self.add_profiler("noop")
        self.add_profiler("unlisted")
        self.run_cli("--package", "p", "--profiler", "all")
        names = [r["profiler"] for r in self._summary()["runs"]]
        self.assertEqual(names, ["noop"])

    def test_explicit_profiler_order_is_preserved(self):
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="a b"\n'
        )
        self.add_profiler("a")
        self.add_profiler("b")
        self.run_cli("--package", "p", "--profiler", "b,a")
        self.assertEqual([r["profiler"] for r in self._summary()["runs"]], ["b", "a"])

    def test_unknown_profiler_in_package_list_is_a_usage_error(self):
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="ghost"\n'
        )
        self.add_profiler("noop")
        r = self.run_cli("--package", "p", "--profiler", "all")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown profiler", r.stderr)

    def test_pinned_run_id_reruns_into_a_cleared_dir(self):
        self._basic_tree()
        self.run_cli("--package", "p", "--profiler", "noop", env_extra={"RUN_ID": "build-1"})
        stale = self.out / "p" / "noop" / "build-1" / "stale.svg"
        stale.write_text("old")
        r = self.run_cli("--package", "p", "--profiler", "noop", env_extra={"RUN_ID": "build-1"})
        self.assertIn("cleared previous artifacts", r.stdout)
        self.assertFalse(stale.exists())

    def test_hooks_fire_in_order_and_post_run_survives_failure(self):
        self.add_package(
            "p",
            env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/false\nPACKAGE_PROFILERS="noop"\n'
            f'TRACE="{self.tmp}/trace"\n',
            script="""
            package_pre_run()  { echo pre  >> "$TRACE"; }
            package_post_run() { echo post >> "$TRACE"; }
            """,
        )
        self.add_profiler("noop")
        self.run_cli("--package", "p", "--profiler", "noop")
        self.assertEqual(
            (self.tmp / "trace").read_text().split(), ["pre", "post"]
        )

    def test_failing_pre_run_aborts_the_run(self):
        self.add_package(
            "p",
            env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="noop"\n',
            script='package_pre_run() { profiling_die "fixture missing"; }\n',
        )
        self.add_profiler("noop")
        r = self.run_cli("--package", "p", "--profiler", "noop")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self._summary()["runs"][0]["status"], "failed")

    def test_profiler_post_runs_only_after_success(self):
        self.add_package(
            "p",
            env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/false\nPACKAGE_PROFILERS="noop"\n'
            f'MARK="{self.tmp}/post-mark"\n',
        )
        self.add_profiler(
            "noop",
            script='profiler_command() { cmd=("${TARGET_ARGV[@]}"); }\n'
            'profiler_post() { touch "$MARK"; }\n',
        )
        self.run_cli("--package", "p", "--profiler", "noop")
        self.assertFalse((self.tmp / "post-mark").exists())

    def test_package_env_overrides_profiler_env(self):
        self.add_package(
            "p",
            env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="noop"\n'
            "KNOB=from-package\n",
        )
        self.add_profiler(
            "noop",
            env='PROFILER_KINDS="exec"\nKNOB=from-profiler\n',
            script='profiler_command() { cmd=(/bin/echo "knob=$KNOB"); }\n',
        )
        self.run_cli("--package", "p", "--profiler", "noop")
        run_id = self._summary()["runs"][0]["run_id"]
        log = (self.out / "p" / "noop" / run_id / "stdout.log").read_text()
        self.assertIn("knob=from-package", log)

    def test_init_hook_runs_when_flagged(self):
        self.add_package(
            "p",
            env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="noop"\n'
            f'PACKAGE_INIT=1\nMARK="{self.tmp}/init-mark"\n',
            script='package_init() { touch "$MARK"; }\n',
        )
        self.add_profiler("noop")
        r = self.run_cli("--package", "p", "--profiler", "noop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.tmp / "init-mark").exists())

    def test_failing_init_yields_exit_4_and_skips_runs(self):
        self.add_package(
            "p",
            env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="noop"\n'
            "PACKAGE_INIT=1\n",
            script='package_init() { return 1; }\n',
        )
        self.add_profiler("noop")
        r = self.run_cli("--package", "p", "--profiler", "noop")
        self.assertEqual(r.returncode, 4)
        self.assertEqual(self._summary()["runs"][0]["status"], "failed")

    def test_package_init_declared_but_absent_is_an_error(self):
        self.add_package(
            "p",
            env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="noop"\n'
            "PACKAGE_INIT=1\n",
        )
        self.add_profiler("noop")
        r = self.run_cli("--package", "p", "--profiler", "noop")
        self.assertEqual(r.returncode, 4)
        self.assertIn("defines no package_init", r.stderr)

    def test_init_flag_ignores_package_init_setting(self):
        self.add_package(
            "p",
            env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\n'
            f'PACKAGE_INIT=0\nMARK="{self.tmp}/init-mark"\n',
            script='package_init() { touch "$MARK"; }\n',
        )
        self.add_profiler("noop")
        r = self.run_cli("--init", "--package", "p")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.tmp / "init-mark").exists())

    def test_init_on_a_package_without_a_hook_is_not_an_error(self):
        self.add_package("p")
        self.add_profiler("noop")
        r = self.run_cli("--init", "--package", "p")
        self.assertEqual(r.returncode, 0)
        self.assertIn("nothing to do", r.stdout)

    def test_warn_if_unset_is_honoured(self):
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="noisy"\n'
        )
        self.add_profiler(
            "noisy",
            env='PROFILER_KINDS="exec"\nPROFILER_WARN_IF_UNSET="NEEDED_KNOB"\n'
            'PROFILER_WARN_MESSAGE="set it in the package .env"\n',
        )
        r = self.run_cli("--package", "p", "--profiler", "noisy")
        self.assertIn("NEEDED_KNOB is unset", r.stderr)
        self.assertIn("set it in the package .env", r.stderr)

    def test_summary_is_written_even_when_a_later_package_aborts(self):
        self.add_package(
            "good", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="noop"\n'
        )
        self.add_package(
            "zbad", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="ghost"\n'
        )
        self.add_profiler("noop")
        r = self.run_cli("--package", "all", "--profiler", "all")
        self.assertEqual(r.returncode, 2)
        self.assertTrue((self.out / "summary.json").exists())
        self.assertEqual(self._summary()["runs"][0]["package"], "good")

    def test_real_env_overrides_package_args(self):
        self._basic_tree()
        self.run_cli(
            "--package", "p", "--profiler", "noop",
            env_extra={"PACKAGE_ARGS": "from-the-environment"},
        )
        run_id = self._summary()["runs"][0]["run_id"]
        log = (self.out / "p" / "noop" / run_id / "stdout.log").read_text()
        self.assertIn("from-the-environment", log)

    def test_remove_output_prunes_and_reports(self):
        self._basic_tree()
        for rid in ("build-1", "build-2"):
            self.run_cli("--package", "p", "--profiler", "noop", env_extra={"RUN_ID": rid})
        r = self.run_cli("--remove-output", "--keep", "1")
        self.assertEqual(r.returncode, 0)
        self.assertIn("removed", r.stdout)
        remaining = sorted(
            d.name for d in (self.out / "p" / "noop").iterdir() if d.is_dir() and not d.is_symlink()
        )
        self.assertEqual(remaining, ["build-2"])


class TestProfilerContract(TreeFixture):
    """A profiler declares a command.  What happens when it declares nothing?"""

    def _summary(self):
        return json.loads((self.out / "summary.json").read_text())

    def test_missing_profiler_command_is_an_error(self):
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="broken"\n'
        )
        self.add_profiler("broken", script='# no profiler_command at all\n')
        r = self.run_cli("--package", "p", "--profiler", "broken")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("defines no profiler_command", r.stderr)

    def test_profiler_command_that_sets_no_cmd_must_not_report_success(self):
        """An empty cmd array runs nothing.  That must not look like a clean run."""
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="empty"\n'
        )
        self.add_profiler("empty", script='profiler_command() { :; }\n')
        r = self.run_cli("--package", "p", "--profiler", "empty")
        self.assertNotEqual(
            r.returncode, 0,
            "a profiler that resolved to no command reported a successful run",
        )

    def test_dry_run_fails_when_the_command_cannot_be_resolved(self):
        """--dry-run is a preflight gate, so a resolution failure must reach the
        exit code and not only stderr."""
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="dies"\n'
        )
        self.add_profiler("dies", script='profiler_command() { profiling_die "nope"; }\n')
        r = self.run_cli("--package", "p", "--profiler", "dies", "--dry-run")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_dry_run_fails_on_an_empty_command(self):
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="empty"\n'
        )
        self.add_profiler("empty", script='profiler_command() { :; }\n')
        r = self.run_cli("--package", "p", "--profiler", "empty", "--dry-run")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_dry_run_still_succeeds_for_a_healthy_profiler(self):
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="noop"\n'
        )
        self.add_profiler("noop")
        r = self.run_cli("--package", "p", "--profiler", "noop", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_broken_package_sh_does_not_silently_run_the_workload(self):
        """A package.sh with a syntax error must not be shrugged off."""
        self.add_package(
            "p",
            env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/echo\nPACKAGE_ARGS=ran\n'
            'PACKAGE_PROFILERS="noop"\n',
            script='package_pre_run() { echo unterminated\n',
        )
        self.add_profiler("noop")
        r = self.run_cli("--package", "p", "--profiler", "noop")
        self.assertNotEqual(
            r.returncode, 0,
            "a package.sh that failed to source still produced a successful run",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
