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
import process  # noqa: E402
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
        for script in ("run_profiling.sh", "install.sh"):
            shutil.copy(HARNESS_ROOT / script, self.root / script)
            os.chmod(self.root / script, 0o755)

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

    def test_plain_assignment_beats_the_ambient_environment(self):
        """The ambient environment is the base, not the winner: a stray variable
        in someone's shell must not silently redirect a run."""
        a = write(self.tmp / "a.env", 'PACKAGE_ARGS="--from-file"\n')
        env = envfile.load_layers(a, real_env={"PACKAGE_ARGS": "--stray-leftover"})
        self.assertEqual(env["PACKAGE_ARGS"], "--from-file")

    def test_ambient_environment_is_visible_as_the_base(self):
        a = write(self.tmp / "a.env", 'DERIVED="$AMBIENT/sub"\n')
        env = envfile.load_layers(a, real_env={"AMBIENT": "/base"})
        self.assertEqual(env["DERIVED"], "/base/sub")
        self.assertEqual(env["AMBIENT"], "/base")

    def test_overrides_are_the_top_layer(self):
        a = write(self.tmp / "a.env", 'KNOB=from-file\n')
        env = envfile.load_layers(a, overrides={"KNOB": "from-cli"}, real_env={})
        self.assertEqual(env["KNOB"], "from-cli")

    def test_overrides_are_visible_to_nothing_beneath_them(self):
        """An override is applied after sourcing, so a `.env` cannot interpolate
        it -- worth pinning so the layering stays honest."""
        a = write(self.tmp / "a.env", 'DERIVED="[$KNOB]"\n')
        env = envfile.load_layers(a, overrides={"KNOB": "late"}, real_env={})
        self.assertEqual(env["KNOB"], "late")
        self.assertEqual(env["DERIVED"], "[]")

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

    def test_malformed_override_is_a_usage_error(self):
        self.add_package("p")
        self.add_profiler("noop")
        for bad in ("NOEQUALS", "1BAD=x", "has-dash=x", "=novalue"):
            r = self.run_cli(
                "--package", "p", "--profiler", "noop", "--env-package", bad
            )
            self.assertEqual(r.returncode, 2, f"{bad!r} was accepted")
            self.assertIn("expects KEY=VALUE", r.stderr)

    def test_malformed_override_is_rejected_before_any_dispatch(self):
        """Parsing happens before dispatch, so the error does not depend on which
        command was asked for."""
        self.add_package("p")
        self.add_profiler("noop")
        r = self.run_cli("--list-packages", "--env-package", "NOEQUALS")
        self.assertEqual(r.returncode, 2)

    def test_empty_override_value_is_allowed(self):
        self.add_package("p")
        self.add_profiler("noop")
        r = self.run_cli("--list-packages", "--env-package", "KNOB=")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_override_value_may_contain_equals_signs(self):
        self.add_package("p")
        self.add_profiler("noop")
        r = self.run_cli("--list-packages", "--env-package", "ARGS=--opt=1 --other=2")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_listing_and_run_agree_on_the_matrix(self):
        """The README says the `profilers` field is exactly what --profiler all
        runs, so a CI matrix built from it must not contain an impossible job."""
        self.add_package(
            "p",
            env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\n'
            'PACKAGE_PROFILERS="real ghost"\n',
        )
        self.add_profiler("real", env='PROFILER_KINDS="exec"\n')

        listed = self.run_cli("--list-packages", "--json")
        ran = self.run_cli("--package", "p", "--profiler", "all")

        self.assertEqual(listed.returncode, 2, listed.stdout + listed.stderr)
        self.assertIn("unknown profiler", listed.stderr)
        self.assertEqual(ran.returncode, listed.returncode)

    def test_listing_matches_what_runs_once_valid(self):
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="b a"\n'
        )
        self.add_profiler("a", env='PROFILER_KINDS="exec"\n')
        self.add_profiler("b", env='PROFILER_KINDS="exec"\n')
        self.add_profiler("unlisted", env='PROFILER_KINDS="exec"\n')

        rows = json.loads(self.run_cli("--list-packages", "--json").stdout)
        self.run_cli("--package", "p", "--profiler", "all")
        ran = [r["profiler"] for r in json.loads((self.out / "summary.json").read_text())["runs"]]
        self.assertEqual(rows[0]["profilers"], ran)

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

    def test_ambient_env_does_not_redirect_a_run(self):
        self._basic_tree()
        self.run_cli(
            "--package", "p", "--profiler", "noop",
            env_extra={"PACKAGE_ARGS": "stray-leftover"},
        )
        run_id = self._summary()["runs"][0]["run_id"]
        log = (self.out / "p" / "noop" / run_id / "stdout.log").read_text()
        self.assertIn("hello", log)
        self.assertNotIn("stray-leftover", log)

    def test_env_package_overrides_a_package_knob(self):
        self._basic_tree()
        self.run_cli(
            "--package", "p", "--profiler", "noop",
            "--env-package", "PACKAGE_ARGS=from-the-command-line",
        )
        run_id = self._summary()["runs"][0]["run_id"]
        log = (self.out / "p" / "noop" / run_id / "stdout.log").read_text()
        self.assertIn("from-the-command-line", log)

    def test_env_package_beats_env_profiler_on_a_collision(self):
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="noop"\n'
        )
        self.add_profiler(
            "noop", script='profiler_command() { cmd=(/bin/echo "knob=$KNOB"); }\n'
        )
        self.run_cli(
            "--package", "p", "--profiler", "noop",
            "--env-profiler", "KNOB=from-profiler-flag",
            "--env-package", "KNOB=from-package-flag",
        )
        run_id = self._summary()["runs"][0]["run_id"]
        log = (self.out / "p" / "noop" / run_id / "stdout.log").read_text()
        self.assertIn("knob=from-package-flag", log)

    def test_overrides_are_recorded_in_meta(self):
        self._basic_tree()
        self.run_cli(
            "--package", "p", "--profiler", "noop",
            "--env-package", "PACKAGE_ARGS=x", "--env-profiler", "RATE=9",
        )
        run_id = self._summary()["runs"][0]["run_id"]
        meta = json.loads((self.out / "p" / "noop" / run_id / "meta.json").read_text())
        self.assertEqual(
            meta["env_overrides"],
            {"profiler": {"RATE": "9"}, "package": {"PACKAGE_ARGS": "x"}},
        )

    def test_overrides_appear_in_dry_run(self):
        self._basic_tree()
        r = self.run_cli(
            "--package", "p", "--profiler", "noop", "--dry-run",
            "--env-package", "PACKAGE_ARGS=shown-in-dry-run",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("shown-in-dry-run", r.stdout)

    def test_repeated_override_of_one_key_takes_the_last(self):
        self._basic_tree()
        self.run_cli(
            "--package", "p", "--profiler", "noop",
            "--env-package", "PACKAGE_ARGS=first",
            "--env-package", "PACKAGE_ARGS=second",
        )
        run_id = self._summary()["runs"][0]["run_id"]
        log = (self.out / "p" / "noop" / run_id / "stdout.log").read_text()
        self.assertIn("second", log)
        self.assertNotIn("first", log)

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


class TestShellHygiene(unittest.TestCase):
    """The bash side is half the harness, so it is linted like the Python side.

    Skips when shellcheck is absent rather than failing: it is a development
    tool, not a runtime dependency.
    """

    def test_every_shell_file_passes_shellcheck(self):
        if shutil.which("shellcheck") is None:
            self.skipTest("shellcheck not installed")
        files = sorted(
            set(HARNESS_ROOT.glob("*.sh"))
            | set(HARNESS_ROOT.glob("lib/*.sh"))
            | set(HARNESS_ROOT.glob("profilers/*/*.sh"))
            | set(HARNESS_ROOT.glob("packages/*/*.sh"))
        )
        self.assertTrue(files, "no shell files found")
        failures = []
        for path in files:
            proc = subprocess.run(
                ["shellcheck", "-x", "-S", "warning", str(path)],
                cwd=HARNESS_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            if proc.returncode != 0:
                failures.append(f"{path.relative_to(HARNESS_ROOT)}:\n{proc.stdout}")
        self.assertEqual(failures, [], "\n".join(failures))


class TestProcessGroup(unittest.TestCase):
    """lib/process.py: POSIX group lifecycle, no profiling knowledge."""

    def test_kill_group_takes_down_a_sigterm_ignoring_child(self):
        proc = subprocess.Popen(
            ["bash", "-c", "trap '' TERM; sleep 60 & wait"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
        )
        pgid = os.getpgid(proc.pid)
        self.assertTrue(process.group_alive(pgid))
        process.kill_group(proc, term_grace=0.5, kill_grace=3.0)
        self.assertFalse(process.group_alive(pgid))

    def test_kill_group_on_an_already_dead_process_is_a_noop(self):
        proc = subprocess.Popen(["true"], start_new_session=True)
        proc.wait()
        process.kill_group(proc)  # must not raise

    def test_group_alive_is_false_for_a_reaped_child(self):
        proc = subprocess.Popen(["true"], start_new_session=True)
        pgid = os.getpgid(proc.pid)
        proc.wait()
        self.assertFalse(process.group_alive(pgid))

    def test_tee_writes_to_both_the_file_and_the_console(self):
        import io

        tmp = Path(tempfile.mkdtemp(prefix="tee-test-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        proc = subprocess.Popen(
            ["printf", "one\ntwo\n"], stdout=subprocess.PIPE
        )
        console = io.StringIO()
        log = tmp / "out.log"
        process.tee(proc.stdout, log, console)
        proc.wait()
        self.assertEqual(log.read_text(), "one\ntwo\n")
        self.assertEqual(console.getvalue(), "one\ntwo\n")


class TestInstaller(TreeFixture):
    """install.sh --check is the documented CI preflight, so it must not pass a
    tree the harness would refuse."""

    def run_install(self, *argv: str):
        return subprocess.run(
            ["bash", str(self.root / "install.sh"), *argv],
            cwd=self.root,
            env=dict(os.environ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )

    def test_check_passes_a_healthy_tree(self):
        self.add_package("p")
        self.add_profiler("noop", env='PROFILER_KINDS="exec"\nPROFILER_REQUIRES_BIN="ls"\n')
        r = self.run_install("--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_check_reports_a_missing_binary(self):
        self.add_package("p")
        self.add_profiler(
            "ghost",
            env='PROFILER_KINDS="exec"\nPROFILER_REQUIRES_BIN="definitely-not-installed-xyz"\n',
        )
        r = self.run_install("--check")
        self.assertEqual(r.returncode, 3)
        self.assertIn("MISSING", r.stdout)

    def test_unsourceable_env_is_refused_not_treated_as_empty(self):
        """A .env that will not source used to come back as silently-empty
        declarations, so --check printed "(none declared)  ok" and exited 0 for a
        tree the harness rejects outright."""
        self.add_package("p")
        self.add_profiler("good", env='PROFILER_KINDS="exec"\nPROFILER_REQUIRES_BIN="ls"\n')
        write(self.root / "profilers" / "broken" / ".env", 'PROFILER_KINDS="unterminated\n')
        write(self.root / "profilers" / "broken" / "profiler.sh", "profiler_command() { :; }\n")

        install = self.run_install("--check")
        harness = self.run_cli("--list-profilers")

        self.assertEqual(install.returncode, 2, install.stdout + install.stderr)
        self.assertIn("broken/.env", install.stderr)
        self.assertNotIn("none declared", install.stdout)
        # The whole point: the two agree that this tree is not runnable.
        self.assertNotEqual(harness.returncode, 0)

    def test_a_broken_package_env_is_also_refused(self):
        self.add_package("p")
        self.add_profiler("noop", env='PROFILER_KINDS="exec"\nPROFILER_REQUIRES_BIN="ls"\n')
        write(self.root / "packages" / "bad" / ".env", 'PACKAGE_ARGS="unterminated\n')
        r = self.run_install("--check")
        self.assertEqual(r.returncode, 2)
        self.assertIn("bad/.env", r.stderr)

    def test_dependencies_are_collected_from_every_entry(self):
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_APT_PACKAGES="pkg-apt"\n'
        )
        self.add_profiler(
            "one", env='PROFILER_KINDS="exec"\nPROFILER_APT_PACKAGES="apt-one"\n'
            'PROFILER_PIP_PACKAGES="pip-one"\n'
        )
        self.add_profiler(
            "two", env='PROFILER_KINDS="exec"\nPROFILER_APT_PACKAGES="apt-two"\n'
        )
        r = self.run_install("--dry-run")
        apt_line = next(
            l for l in r.stdout.splitlines() if l.startswith("Discovered apt")
        )
        for expected in ("apt-one", "apt-two", "pkg-apt"):
            self.assertIn(expected, apt_line)
        self.assertIn("pip-one", r.stdout)

    def test_templates_are_excluded_from_discovery(self):
        self.add_package("p")
        self.add_profiler("noop", env='PROFILER_KINDS="exec"\nPROFILER_REQUIRES_BIN="ls"\n')
        write(
            self.root / "profilers" / "_template" / ".env",
            'PROFILER_APT_PACKAGES="should-not-be-installed"\n',
        )
        r = self.run_install("--dry-run")
        self.assertNotIn("should-not-be-installed", r.stdout)


class TestSignals(TreeFixture):
    """The workload runs in its own session, so a signal to the harness does not
    reach it.  An interrupt must therefore take the process group down."""

    def _long_run(self):
        self.add_package(
            "slow",
            env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/sleep\nPACKAGE_ARGS=300\n'
            'PACKAGE_PROFILERS="noop"\n',
        )
        self.add_profiler("noop")

    def _interrupt_with(self, sig: int):
        import signal as _signal
        import time as _time

        self._long_run()
        proc = subprocess.Popen(
            [sys.executable, str(self.root / "lib" / "cli.py"),
             "--package", "slow", "--profiler", "noop"],
            cwd=self.root,
            env={k: v for k, v in os.environ.items() if k != "RUN_ID"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = _time.time() + 20
        while _time.time() < deadline:
            if subprocess.run(
                ["pgrep", "-f", "^/bin/sleep 300$"], stdout=subprocess.DEVNULL
            ).returncode == 0:
                break
            _time.sleep(0.1)
        else:
            proc.kill()
            self.fail("workload never started")

        proc.send_signal(sig)
        returncode = proc.wait(timeout=30)

        deadline = _time.time() + 10
        while _time.time() < deadline:
            alive = subprocess.run(
                ["pgrep", "-f", "^/bin/sleep 300$"], stdout=subprocess.DEVNULL
            ).returncode == 0
            if not alive:
                break
            _time.sleep(0.2)
        else:
            subprocess.run(["pkill", "-9", "-f", "^/bin/sleep 300$"])
            self.fail(f"signal {sig} orphaned the workload")
        self.assertEqual(returncode, 1)

    def test_sigint_kills_the_workload(self):
        import signal as _signal
        self._interrupt_with(_signal.SIGINT)

    def test_sigterm_kills_the_workload(self):
        import signal as _signal
        self._interrupt_with(_signal.SIGTERM)

    def test_sighup_kills_the_workload(self):
        import signal as _signal
        self._interrupt_with(_signal.SIGHUP)


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

    def test_a_run_that_resolves_to_nothing_is_an_error(self):
        """Zero (package, profiler) pairs used to print nothing and exit 0."""
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS=""\n'
        )
        r = self.run_cli(
            "--package", "all", "--profiler", "all",
            # A blank, not empty: config.env uses `: "${VAR:=default}"`, which
            # treats an empty ambient value as unset and restores the default.
            env_extra={"DEFAULT_PROFILERS": " "},
        )
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("nothing ran", r.stderr)
        self.assertIn("no profiler applies to p", r.stderr)

    def test_dry_run_also_fails_when_nothing_resolves(self):
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS=""\n'
        )
        r = self.run_cli(
            "--package", "all", "--profiler", "all", "--dry-run",
            # A blank, not empty: config.env uses `: "${VAR:=default}"`, which
            # treats an empty ambient value as unset and restores the default.
            env_extra={"DEFAULT_PROFILERS": " "},
        )
        self.assertEqual(r.returncode, 2)

    def test_all_pairs_skipped_is_still_a_success(self):
        """Skips are outcomes, so they must not trip the nothing-ran check."""
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="pyonly"\n'
        )
        self.add_profiler("pyonly", env='PROFILER_KINDS="python-module"\n')
        r = self.run_cli("--package", "all", "--profiler", "all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._summary()["counts"]["skipped"], 1)

    def test_misspelled_kind_is_an_error_not_a_skip(self):
        """A typo in PACKAGE_KIND must not masquerade as an incompatible kind:
        every profiler would skip and the invocation would still exit 0."""
        self.add_package(
            "p",
            env='PACKAGE_KIND=python-modul\nPACKAGE_ENTRY=my.mod\n'
            'PACKAGE_PROFILERS="a b"\n',
        )
        for name in ("a", "b"):
            self.add_profiler(name, env='PROFILER_KINDS="python-module exec"\n')
        r = self.run_cli("--package", "p", "--profiler", "all")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("unknown PACKAGE_KIND", r.stderr)
        self.assertNotIn("SKIP", r.stdout)

    def test_misspelled_kind_is_caught_by_dry_run_too(self):
        self.add_package(
            "p",
            env='PACKAGE_KIND=exek\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="noop"\n',
        )
        self.add_profiler("noop")
        r = self.run_cli("--package", "p", "--profiler", "noop", "--dry-run")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_a_genuinely_incompatible_kind_is_still_a_skip(self):
        """The fix must not turn real kind mismatches into failures."""
        self.add_package(
            "p", env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="pyonly"\n'
        )
        self.add_profiler("pyonly", env='PROFILER_KINDS="python-module"\n')
        r = self.run_cli("--package", "p", "--profiler", "all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SKIP", r.stdout)

    def test_malformed_timeout_fails_dry_run(self):
        """--dry-run is a preflight gate, so it must reject a value that would
        abort the real run."""
        self.add_package(
            "p",
            env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_PROFILERS="noop"\n'
            "PACKAGE_TIMEOUT=soon\n",
        )
        self.add_profiler("noop")
        dry = self.run_cli("--package", "p", "--profiler", "noop", "--dry-run")
        real = self.run_cli("--package", "p", "--profiler", "noop")
        self.assertEqual(dry.returncode, 2, dry.stdout + dry.stderr)
        self.assertIn("PACKAGE_TIMEOUT must be a number", dry.stderr)
        self.assertEqual(real.returncode, 2)

    def test_dry_run_prints_exactly_what_the_real_run_executes(self):
        """resolve() and execute() now share one preparation step, so this pins
        the claim the design makes: the printed command *is* the command."""
        import shlex as _shlex

        self.add_package(
            "p",
            env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/echo\nPACKAGE_ARGS="a b"\n'
            'PACKAGE_PROFILERS="wrap"\n',
        )
        self.add_profiler(
            "wrap",
            script='profiler_command() { cmd=(/bin/echo "--out" "$(run_artifact o.txt)" '
            '-- "${TARGET_ARGV[@]}"); }\n',
        )
        # A pinned RUN_ID makes both invocations name the same run directory, so
        # the two commands are comparable character for character.
        pin = {"RUN_ID": "compare-1"}
        dry = self.run_cli("--package", "p", "--profiler", "wrap", "--dry-run", env_extra=pin)
        self.run_cli("--package", "p", "--profiler", "wrap", env_extra=pin)

        printed = next(
            l.split("command : ", 1)[1]
            for l in dry.stdout.splitlines()
            if "command : " in l
        )
        meta = json.loads(
            (self.out / "p" / "wrap" / "compare-1" / "meta.json").read_text()
        )
        executed = " ".join(_shlex.quote(a) for a in meta["command"])
        self.assertEqual(printed, executed)

    def test_init_rejects_a_package_sh_that_will_not_source(self):
        self.add_package(
            "p",
            env='PACKAGE_KIND=exec\nPACKAGE_ENTRY=/bin/true\nPACKAGE_INIT=1\n',
            script='package_init() { echo unterminated\n',
        )
        self.add_profiler("noop")
        r = self.run_cli("--init", "--package", "p")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("failed to source", r.stderr)

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
