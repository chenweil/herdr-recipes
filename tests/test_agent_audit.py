import json
import os
import pathlib
import subprocess
import tempfile
import textwrap
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
AUDIT = REPO_ROOT / "scripts" / "agent-audit.py"

FAKE_HERDR = textwrap.dedent(
    r"""
    #!/usr/bin/env python3
    import os
    import pathlib
    import sys

    args = sys.argv[1:]
    log_path = os.environ.get("FAKE_HERDR_LOG")
    if log_path:
        with pathlib.Path(log_path).open("a") as log:
            log.write(" ".join(args) + "\n")
    if args == ["--version"]:
        print(os.environ.get("FAKE_HERDR_VERSION", "herdr 0.8.2"))
        raise SystemExit(0)
    if args[:3] == ["agent", "start", "--help"]:
        print(os.environ.get("FAKE_HERDR_HELP", ""))
        raise SystemExit(int(os.environ.get("FAKE_HERDR_HELP_EXIT", "0")))
    if args[:2] == ["integration", "status"]:
        print(os.environ.get("FAKE_HERDR_INTEGRATIONS", ""))
        raise SystemExit(int(os.environ.get("FAKE_HERDR_STATUS_EXIT", "0")))
    if args[:2] in (["config", "check"], ["server", "reload-config"]):
        raise SystemExit(0)
    print("unexpected fake herdr command: " + " ".join(args), file=sys.stderr)
    raise SystemExit(2)
    """
).lstrip()


class AgentAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp_dir.name)
        self.bin_dir = root / "bin"
        self.bin_dir.mkdir()
        fake_herdr = self.bin_dir / "herdr"
        fake_herdr.write_text(FAKE_HERDR)
        fake_herdr.chmod(0o755)
        self.catalog_path = root / "catalog.json"
        self.log_path = root / "herdr.log"
        self.environment = os.environ.copy()
        self.environment["FAKE_HERDR_LOG"] = str(self.log_path)
        self.environment["PATH"] = str(self.bin_dir) + os.pathsep + self.environment["PATH"]

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_catalog(self, kinds, aliases=None):
        self.catalog_path.write_text(
            json.dumps(
                {
                    "catalog_version": 1,
                    "min_herdr_version": "0.8.2",
                    "aliases": aliases or {},
                    "kinds": kinds,
                }
            )
        )

    def test_repository_catalog_covers_all_herdr_0_9_1_kinds_and_aliases(self):
        catalog = json.loads((REPO_ROOT / "config" / "agent-catalog.json").read_text())
        expected = {
            "pi", "claude", "codex", "gemini", "cursor", "devin", "agy", "cline",
            "omp", "mastracode", "opencode", "copilot", "kimi", "kiro", "droid",
            "amp", "grok", "hermes", "kilo", "qodercli", "qwen", "maki",
            # added against Herdr 0.9.1
            "letta", "muse",
        }

        self.assertEqual({entry["kind"] for entry in catalog["kinds"]}, expected)
        self.assertEqual(catalog["aliases"], {"op": "opencode", "cc": "claude", "cd": "codex"})
        self.assertIn("cursor-agent", next(entry for entry in catalog["kinds"] if entry["kind"] == "cursor")["executables"])
        self.assertEqual(
            next(entry for entry in catalog["kinds"] if entry["kind"] == "agy")["integration_targets"],
            ["antigravity-cli"],
        )

    def run_audit(self, *args):
        return subprocess.run(
            ["python3", str(AUDIT), "--catalog", str(self.catalog_path), *args],
            cwd=REPO_ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
        )

    def test_reports_executable_and_integration_status_separately(self):
        self.write_catalog(
            [
                {
                    "kind": "cursor",
                    "executables": ["cursor-agent"],
                    "integration_targets": ["cursor"],
                },
                {
                    "kind": "pi",
                    "executables": ["missing-pi"],
                    "integration_targets": ["pi"],
                },
            ]
        )
        cursor_agent = self.bin_dir / "cursor-agent"
        cursor_agent.write_text("#!/bin/sh\nexit 0\n")
        cursor_agent.chmod(0o755)
        self.environment["FAKE_HERDR_HELP"] = """\
Usage: herdr agent start <NAME> --kind <KIND> --pane <ID>
      [possible values: cursor, pi]
"""
        self.environment["FAKE_HERDR_INTEGRATIONS"] = (
            "cursor: current (v8) (/tmp/cursor)\n"
            "pi: not installed (/tmp/pi)\n"
        )

        result = self.run_audit()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cursor: executable=found (cursor-agent); integration=current (cursor)", result.stdout)
        self.assertIn("pi: executable=missing (missing-pi); integration=missing (pi)", result.stdout)

    def test_unknown_kind_from_future_herdr_is_not_rejected(self):
        self.write_catalog(
            [{"kind": "pi", "executables": ["missing-pi"], "integration_targets": []}]
        )
        self.environment["FAKE_HERDR_HELP"] = "[possible values: pi, future-agent]"

        result = self.run_audit()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("future-agent: availability unknown", result.stdout)

    def test_malformed_help_warns_and_falls_back_to_catalog(self):
        self.write_catalog(
            [{"kind": "pi", "executables": ["missing-pi"], "integration_targets": []}]
        )
        self.environment["FAKE_HERDR_HELP"] = "help output without a values list"

        result = self.run_audit()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("could not parse herdr agent start --help", result.stderr)
        self.assertIn("pi: executable=missing", result.stdout)

    def test_old_herdr_version_fails_the_explicit_version_check(self):
        self.write_catalog(
            [{"kind": "pi", "executables": ["pi"], "integration_targets": []}]
        )
        self.environment["FAKE_HERDR_VERSION"] = "herdr 0.8.1"

        result = self.run_audit("--check-version")

        self.assertEqual(result.returncode, 2)
        self.assertIn("too old", result.stderr)

    def test_integration_status_failure_is_a_warning_with_unknown_status(self):
        self.write_catalog(
            [{"kind": "pi", "executables": ["missing-pi"], "integration_targets": ["pi"]}]
        )
        self.environment["FAKE_HERDR_HELP"] = "[possible values: pi]"
        self.environment["FAKE_HERDR_STATUS_EXIT"] = "1"

        result = self.run_audit()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("could not read herdr integration status", result.stderr)
        self.assertIn("pi: executable=missing (missing-pi); integration=unknown (pi)", result.stdout)

    def test_reports_outdated_integration_status_when_reported(self):
        self.write_catalog(
            [{"kind": "pi", "executables": ["missing-pi"], "integration_targets": ["pi"]}]
        )
        self.environment["FAKE_HERDR_HELP"] = "[possible values: pi]"
        self.environment["FAKE_HERDR_INTEGRATIONS"] = "pi: outdated (v7 -> v8) (/tmp/pi)\n"

        result = self.run_audit()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("integration=outdated (pi)", result.stdout)

    def test_letta_experimental_marker_strips_to_target_match(self):
        # Herdr 0.9.1 wraps experimental kinds in " (...) " (e.g.
        # "letta (experimental)"). Without the suffix strip in
        # parse_integration_status the target key becomes
        # "letta (experimental)" which never matches the catalog
        # integration_targets entry ["letta"], and the audit falls back
        # to "unknown" instead of reporting "missing". This pins the
        # strip behavior so the audit correctly reports the install gap.
        self.write_catalog(
            [{"kind": "letta", "executables": ["letta"], "integration_targets": ["letta"]}]
        )
        self.environment["FAKE_HERDR_HELP"] = "[possible values: letta]"
        self.environment["FAKE_HERDR_INTEGRATIONS"] = "letta (experimental): not installed (/tmp/letta)\n"

        result = self.run_audit()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("integration=missing (letta)", result.stdout)


class InstallerAuditTests(AgentAuditTests):
    def run_install(self, version="herdr 0.8.2"):
        root = pathlib.Path(self.temp_dir.name)
        home = root / "home"
        herdr_home = home / ".config" / "herdr"
        herdr_home.mkdir(parents=True, exist_ok=True)
        config = herdr_home / "config.toml"
        if not config.exists():
            config.write_text("[keys]\nprefix = \"cmd+b\"\n")
        environment = self.environment.copy()
        environment.update(
            {
                "HOME": str(home),
                "HERDR_HOME": str(herdr_home),
                "FAKE_HERDR_VERSION": version,
                "FAKE_HERDR_HELP": "[possible values: cursor, pi]",
                "FAKE_HERDR_INTEGRATIONS": "cursor: not installed (/tmp/cursor)\npi: current (v8) (/tmp/pi)\n",
            }
        )
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "install.sh")],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        return result, herdr_home, config

    def test_install_audits_idempotently_without_installing_integrations(self):
        first, herdr_home, config = self.run_install()
        second, _, second_config = self.run_install()

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertTrue((herdr_home / "scripts").is_symlink())
        self.assertEqual((herdr_home / "scripts").resolve(), REPO_ROOT / "scripts")
        self.assertEqual(second_config.read_text().count("herdr-recipes managed: begin"), 1)
        log = self.log_path.read_text()
        self.assertIn("integration status", log)
        self.assertNotIn("integration install", log)
        self.assertIn("agent audit", first.stdout)

    def test_install_rejects_old_herdr_before_mutating_isolated_home(self):
        result, herdr_home, config = self.run_install("herdr 0.8.1")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("too old", result.stderr)
        self.assertFalse((herdr_home / "scripts").exists())
        self.assertEqual(config.read_text(), "[keys]\nprefix = \"cmd+b\"\n")
        # Version gate runs before backup_if_exists; rejection must not
        # create any .bak side-effect on the isolated herdr home.
        self.assertEqual(list(herdr_home.glob("config.toml.bak.*")), [])

    def run_uninstall(self, herdr_home):
        root = pathlib.Path(self.temp_dir.name)
        environment = self.environment.copy()
        environment.update(
            {
                "HOME": str(root / "home"),
                "HERDR_HOME": str(herdr_home),
                "FAKE_HERDR_VERSION": "herdr 0.8.2",
                "FAKE_HERDR_HELP": "[possible values: cursor, pi]",
                "FAKE_HERDR_INTEGRATIONS": "cursor: not installed (/tmp/cursor)\npi: current (v8) (/tmp/pi)\n",
            }
        )
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "install.sh"), "--uninstall"],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        return result, herdr_home

    def test_install_creates_config_toml_backup_equal_to_pre_install_content(self):
        # Pre-populate the isolated config with user-authored content that
        # includes a managed block reference plus a user binding that must
        # survive install. The backup taken by install must capture this
        # exact byte-for-byte content.
        original_content = (
            "[keys]\n"
            "prefix = \"cmd+b\"\n"
            "\n"
            "[[keys.command]]\n"
            "key = \"prefix+1\"\n"
            'command = "cd recipes"\n'
        )
        _, herdr_home, config = self.run_install()  # scaffold isolated home
        config.write_text(original_content)

        result, herdr_home, config = self.run_install()

        self.assertEqual(result.returncode, 0, result.stderr)
        matching = [
            b for b in herdr_home.glob("config.toml.bak.*")
            if b.read_text() == original_content
        ]
        self.assertEqual(
            len(matching), 1,
            f"expected exactly one backup matching pre-install content; got {[b.name for b in matching]}",
        )

    def test_uninstall_creates_config_toml_backup_equal_to_pre_uninstall_content(self):
        # Install first so the config carries a managed block; that is the
        # state uninstall will see and must back up before stripping.
        install_result, herdr_home, config = self.run_install()
        self.assertEqual(install_result.returncode, 0, install_result.stderr)
        pre_uninstall_content = config.read_text()
        self.assertIn("herdr-recipes managed: begin", pre_uninstall_content)

        uninstall_result, herdr_home = self.run_uninstall(herdr_home)

        self.assertEqual(uninstall_result.returncode, 0, uninstall_result.stderr)
        matching = [
            b for b in herdr_home.glob("config.toml.bak.*")
            if b.read_text() == pre_uninstall_content
        ]
        self.assertEqual(
            len(matching), 1,
            f"expected exactly one backup matching pre-uninstall content; got {[b.name for b in matching]}",
        )

    def test_install_does_not_overwrite_previous_backup_when_called_twice_in_same_second(self):
        # Two installs in the same wall-clock second must not collide on
        # the backup name; the first backup (containing the original
        # user content) must survive even if its timestamp matches the
        # second run.
        original_content = (
            "[keys]\n"
            "prefix = \"cmd+b\"\n"
            "[[keys.command]]\n"
            "key = \"prefix+1\"\n"
            'command = "cd recipes"\n'
        )
        _, herdr_home, config = self.run_install()  # scaffold isolated home
        config.write_text(original_content)
        # Drop the scaffold-run's own backup so this test sees exactly the
        # two backups produced by the two same-second installs below.
        for leftover in herdr_home.glob("config.toml.bak.*"):
            leftover.unlink()

        first, herdr_home, config = self.run_install()
        second, herdr_home, config = self.run_install()

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        backups = sorted(herdr_home.glob("config.toml.bak.*"), key=lambda p: p.name)
        self.assertEqual(
            len(backups), 2,
            f"expected exactly two backups; got {[b.name for b in backups]}",
        )
        # The first backup (lexicographic name order keeps the un-suffixed
        # one first) must still hold the original user content. If
        # backup_if_exists had silently overwritten it, this would fail.
        self.assertEqual(backups[0].read_text(), original_content)
        # The second backup captures the post-install state (original
        # plus the newly written managed block), so it must differ.
        self.assertNotEqual(backups[1].read_text(), original_content)

    def test_install_removes_legacy_prefix_alt_8_then_inserts_managed_copy(self):
        # Pre-populate the isolated config with a hand-written
        # `prefix+alt+8` binding that simulates a user who manually wired
        # 221 before #8 shipped. After install, that key must appear
        # exactly once in config.toml (legacy removed + managed block
        # inserted) and the surviving occurrence must sit inside the
        # managed block. This pins the legacy_keys regex range to 1..9.
        _, herdr_home, config = self.run_install()  # scaffold isolated home
        pre_content = config.read_text()
        legacy_block = (
            "[[keys.command]]\n"
            "key = \"prefix+alt+8\"\n"
            'command = "bash ~/.config/herdr/scripts/hopen.sh 221"\n'
            "description = \"legacy user-bound alt+8\"\n"
        )
        config.write_text(pre_content + "\n" + legacy_block + "\n")

        result, herdr_home, config = self.run_install()

        self.assertEqual(result.returncode, 0, result.stderr)
        final_text = config.read_text()
        key_occurrences = final_text.count('key = "prefix+alt+8"')
        self.assertEqual(
            key_occurrences, 1,
            f"prefix+alt+8 should appear exactly once after install "
            f"(legacy removed + managed inserted); got {key_occurrences}",
        )
        begin = "# >>> herdr-recipes managed: begin >>>"
        end = "# <<< herdr-recipes managed: end <<<"
        self.assertIn(begin, final_text)
        self.assertIn(end, final_text)
        managed_slice = final_text.split(begin, 1)[1].split(end, 1)[0]
        self.assertIn(
            'key = "prefix+alt+8"', managed_slice,
            "the surviving prefix+alt+8 binding should live inside the managed block",
        )


if __name__ == "__main__":
    unittest.main()
