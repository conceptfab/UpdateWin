import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import update


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_npm_outdated_skips_linked_and_takes_first_location(self):
        out = json.dumps({
            "typescript": {"current": "5.5.0", "wanted": "5.6.3", "latest": "5.6.3"},
            "dup": [{"current": "1.0.0", "latest": "2.0.0"}, {"current": "0.9.0", "latest": "2.0.0"}],
            "mylib": {"current": "1.0.0", "latest": "linked"},
        })
        items = update.parse_npm_outdated(out)
        self.assertEqual([(i.name, i.installed, i.latest) for i in items],
                         [("dup", "1.0.0", "2.0.0"), ("typescript", "5.5.0", "5.6.3")])
        self.assertEqual(update.parse_npm_outdated(""), [])
        with self.assertRaises(RuntimeError):
            update.parse_npm_outdated('{"error": {"summary": "boom"}}')

    def test_npx_scan_marks_exact_version_requests_as_pinned(self):
        d = self.tmp / "_npx" / "abc"
        write(d / "package.json", {"dependencies": {"cowsay": "*", "mcp": "1.2.3"},
                                   "_npx": {"packages": ["cowsay", "mcp@1.2.3"]}})
        write(d / "node_modules" / "cowsay" / "package.json", {"version": "1.5.0"})
        entries = update.scan_npx(self.tmp / "_npx")
        self.assertEqual([(n, v, p) for n, v, _, p in entries], [("cowsay", "1.5.0", False), ("mcp", None, True)])
        self.assertEqual(update.scan_npx(self.tmp / "missing"), [])

    def test_npx_check_reports_oldest_cached_version_with_all_dirs(self):
        for h, v in (("a", "1.4.0"), ("b", "1.5.0")):
            write(self.tmp / "_npx" / h / "package.json", {"dependencies": {"cowsay": "*"}})
            write(self.tmp / "_npx" / h / "node_modules" / "cowsay" / "package.json", {"version": v})
        with mock.patch.object(update, "npx_root", return_value=self.tmp / "_npx"), \
             mock.patch.object(update, "tool", return_value="npm"), \
             mock.patch.object(update, "npm_view", return_value="1.6.0"):
            items = update.check_npx()
        self.assertEqual((items[0].installed, items[0].latest, len(items[0].dirs)), ("1.4.0", "1.6.0", 2))

    def test_pip_outdated(self):
        out = 'WARNING x\n[{"name": "ruff", "version": "0.6.9", "latest_version": "0.7.2"}]'
        self.assertEqual([(i.name, i.latest) for i in update.parse_pip_outdated(out)], [("ruff", "0.7.2")])

    def test_claude_plugins_use_plugin_json_then_catalog_entry(self):
        market = self.tmp / "marketplaces" / "wf"
        write(self.tmp / "known_marketplaces.json", {"wf": {"installLocation": str(market)}})
        write(market / ".claude-plugin" / "marketplace.json", {"plugins": [
            {"name": "ui", "version": "9.9.9", "source": "./plugins/ui"},
            {"name": "same", "version": "1.0.2", "source": "./plugins/same"},
            {"name": "remote", "version": "2.0.0", "source": {"source": "url"}},
        ]})
        write(market / "plugins" / "ui" / ".claude-plugin" / "plugin.json", {"version": "1.0.4"})
        out = json.dumps([{"id": "ui@wf", "version": "1.0.3"}, {"id": "ui@wf", "version": "1.0.3"},
                          {"id": "same@wf", "version": "1.0.2"}, {"id": "remote@wf", "version": "1.0.0"},
                          {"id": "figma@synced", "version": "2.0"}])
        items = update.parse_claude_plugins("warn\n" + out, self.tmp)
        self.assertEqual([(i.name, i.latest) for i in items], [("ui@wf", "1.0.4"), ("remote@wf", "2.0.0")])

    def test_codex_plugins_compare_cache_folder_with_marketplace_version(self):
        (self.tmp / "ponytail" / "ponytail" / "5.3.2").mkdir(parents=True)
        (self.tmp / "b" / "browser" / "1.0").mkdir(parents=True)
        out = json.dumps({"installed": [
            {"pluginId": "ponytail@ponytail", "name": "ponytail", "marketplaceName": "ponytail", "version": "5.3.3"},
            {"pluginId": "browser@b", "name": "browser", "marketplaceName": "b", "version": "1.0"},
            {"pluginId": "x@none", "name": "x", "marketplaceName": "none", "version": "1.0"},
        ]})
        items = update.parse_codex_plugins(out, self.tmp)
        self.assertEqual([(i.name, i.installed, i.latest) for i in items], [("ponytail@ponytail", "5.3.2", "5.3.3")])

    def test_skills_compare_lock_hash_with_upstream_folder(self):
        lock = {"skills": {
            "same": {"source": "o/r", "skillPath": "skills/same/SKILL.md", "skillFolderHash": "aaaaaaaaa"},
            "changed": {"source": "o/r", "skillPath": "changed/SKILL.md", "skillFolderHash": "bbbbbbbbb"},
            "gone": {"source": "o/r", "skillPath": "gone/SKILL.md", "skillFolderHash": "ccccccccc"},
        }}
        trees = {"o/r": {"skills/same": "aaaaaaaaa", "changed": "123456789"}}
        self.assertEqual([(i.name, i.installed, i.latest) for i in update.compare_skills(lock, trees)],
                         [("changed", "bbbbbbb", "1234567")])

    def test_safe_names_reject_options_and_shell_metacharacters(self):
        self.assertEqual(update.safe(["@anthropic-ai/claude-code", "ui@wf", "--global", "a&calc", "x|y", "ok.pkg"]),
                         ["@anthropic-ai/claude-code", "ui@wf", "ok.pkg"])

    def test_update_never_runs_bare_install(self):
        with mock.patch.object(update, "call") as call:
            self.assertTrue(update.update_npm([update.Item("--evil", "1", "2")]))
            self.assertTrue(update.update_pip("python", [update.Item("a&b", "1", "2")]))
        call.assert_not_called()

    def test_npx_update_only_deletes_inside_cache(self):
        root = self.tmp / "_npx"
        inside, outside = root / "h1", self.tmp / "keep"
        inside.mkdir(parents=True)
        outside.mkdir()
        with mock.patch.object(update, "npx_root", return_value=root):
            update.update_npx([update.Item("p", "1", "2", [inside, outside])])
        self.assertFalse(inside.exists())
        self.assertTrue(outside.exists())

    def test_env_drops_venv_variables_for_child_python(self):
        for name in update.PY_LEAKS:
            self.assertNotIn(name, update.ENV)

    def test_sync_copies_bundled_skills_only_where_missing(self):
        import claude_sync
        bundle, local = self.tmp / "bundle", self.tmp / "local"
        write(bundle / "gone-upstream" / "SKILL.md", "a")
        write(bundle / "kept" / "SKILL.md", "new")
        write(local / "kept" / "SKILL.md", "old")
        with mock.patch.object(claude_sync, "BUNDLE", bundle), mock.patch.object(claude_sync, "LOCAL_SKILLS", local):
            missing = claude_sync.diff({"localSkills": [".trash", "gone-upstream", "kept", "nowhere"]},
                                       {"marketplaces": {}, "plugins": [], "skills": {}, "localSkills": ["kept"]})
            self.assertEqual(missing["localSkills"], ["gone-upstream", "nowhere"])
            self.assertEqual(claude_sync.copy_bundled(missing["localSkills"]), ["gone-upstream"])
        self.assertTrue((local / "gone-upstream" / "SKILL.md").exists())
        self.assertEqual((local / "kept" / "SKILL.md").read_text(encoding="utf-8"), "old")


if __name__ == "__main__":
    unittest.main()
