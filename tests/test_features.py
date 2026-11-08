import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.features import (
    FEATURES, Feature, FeatureRegistry, FeatureUnavailable, PROFILES,
    ALL, BIG,
)


def ram(total_gb):
    return lambda: {"total_gb": total_gb, "available_gb": total_gb / 2,
                    "percent": 50}


def no_ram():
    return None


TEST_FEATURES = (
    Feature("basic", "Basic thing", "Always fine.", ALL, aliases=("simple",)),
    Feature("needs_pkg", "Package thing", "Needs json.", ALL,
            packages=("json",)),
    Feature("missing_pkg", "Missing thing", "Needs a ghost.", ALL,
            packages=("no_such_package_xyz",)),
    Feature("big_ram", "Big thing", "Needs 6 GB.", ALL, min_total_gb=6.0),
    Feature("big_only", "Big only thing", "Only in big profiles.", BIG),
    Feature("future", "Future thing", "Not built.", ALL, planned="9.9"),
)


class RegistryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self.tmp.name) / "data" / "settings.json"

    def tearDown(self):
        self.tmp.cleanup()

    def registry(self, total=3.83, path=True, features=TEST_FEATURES):
        return FeatureRegistry(self.path if path else None,
                               memory_status=ram(total) if total else no_ram,
                               features=features)


class ProfileTests(RegistryTestCase):
    def test_default_profile_is_the_safe_one(self):
        registry = self.registry()
        self.assertEqual(registry.profile, "lite")
        self.assertFalse(registry.profile_chosen)

    def test_recommendation_follows_installed_ram(self):
        for total, expected in ((3.83, "lite"), (6.9, "lite"), (7.8, "standard"),
                                (15.9, "full"), (32.0, "full")):
            with self.subTest(total=total):
                self.assertEqual(self.registry(total).recommended_profile(),
                                 expected)

    def test_recommendation_unknown_without_ram_info(self):
        self.assertIsNone(self.registry(total=None).recommended_profile())

    def test_set_profile_changes_defaults(self):
        registry = self.registry(total=15.9)
        self.assertEqual(registry.status("big_only").state, "disabled")
        registry.set_profile("full")
        self.assertTrue(registry.profile_chosen)
        self.assertEqual(registry.status("big_only").state, "enabled")

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            self.registry().set_profile("ultra")

    def test_profile_that_exceeds_the_pc_gives_a_warning(self):
        registry = self.registry(total=3.83)
        self.assertEqual(registry.profile_warning(), "")
        registry.set_profile("standard")
        self.assertIn("3.8 GB", registry.profile_warning())

    def test_real_feature_table_is_consistent(self):
        keys = [feature.key for feature in FEATURES]
        self.assertEqual(len(keys), len(set(keys)))
        for feature in FEATURES:
            self.assertTrue(feature.profiles <= set(PROFILES))


class StatusTests(RegistryTestCase):
    def test_profile_default_on(self):
        status = self.registry().status("basic")
        self.assertEqual((status.state, status.source), ("enabled", "profile"))

    def test_profile_default_off_says_why(self):
        status = self.registry().status("big_only")
        self.assertEqual(status.state, "disabled")
        self.assertIn("lite profile", status.reason)

    def test_user_override_beats_profile_both_ways(self):
        registry = self.registry()
        registry.set_enabled("big_only", True)
        self.assertTrue(registry.is_enabled("big_only"))
        registry.set_enabled("basic", False)
        status = registry.status("basic")
        self.assertEqual((status.state, status.source), ("disabled", "you"))
        self.assertIn("turned off by you", status.reason)

    def test_reset_returns_to_profile_default(self):
        registry = self.registry()
        registry.set_enabled("basic", False)
        registry.set_enabled("basic", None)
        self.assertTrue(registry.is_enabled("basic"))
        self.assertIsNone(registry.override("basic"))

    def test_packages_are_checked_without_importing_them(self):
        registry = self.registry()
        self.assertEqual(registry.status("needs_pkg").state, "enabled")
        missing = registry.status("missing_pkg")
        self.assertEqual(missing.state, "unavailable")
        self.assertIn("no_such_package_xyz", missing.reason)

    def test_missing_package_blocks_even_an_explicit_enable(self):
        registry = self.registry()
        registry.set_enabled("missing_pkg", True)
        self.assertEqual(registry.status("missing_pkg").state, "unavailable")

    def test_too_little_ram_blocks_a_profile_default(self):
        status = self.registry(total=3.83).status("big_ram")
        self.assertEqual(status.state, "unavailable")
        self.assertIn("6 GB", status.reason)
        self.assertIn("3.8 GB", status.reason)

    def test_user_can_force_a_ram_hungry_feature_with_a_warning(self):
        registry = self.registry(total=3.83)
        registry.set_enabled("big_ram", True)
        status = registry.status("big_ram")
        self.assertEqual(status.state, "enabled")
        self.assertIn("6 GB", status.warning)

    def test_enough_ram_needs_no_warning(self):
        status = self.registry(total=7.8).status("big_ram")
        self.assertEqual((status.state, status.warning), ("enabled", ""))

    def test_unknown_ram_does_not_block(self):
        self.assertTrue(self.registry(total=None).is_enabled("big_ram"))

    def test_unbuilt_features_are_unavailable_even_if_enabled(self):
        registry = self.registry()
        registry.set_enabled("future", True)
        status = registry.status("future")
        self.assertEqual(status.state, "unavailable")
        self.assertIn("v9.9", status.reason)

    def test_unknown_key_raises(self):
        with self.assertRaises(KeyError):
            self.registry().is_enabled("nope")
        with self.assertRaises(KeyError):
            self.registry().set_enabled("nope", True)


class ResolveTests(RegistryTestCase):
    def setUp(self):
        super().setUp()
        self.registry_ = FeatureRegistry(memory_status=ram(8))

    def key(self, text):
        return self.registry_.resolve(text).key

    def test_exact_names_aliases_and_keys(self):
        self.assertEqual(self.key("ram_monitor"), "ram_monitor")
        self.assertEqual(self.key("RAM monitor"), "ram_monitor")
        self.assertEqual(self.key("ram"), "ram_monitor")
        self.assertEqual(self.key("llm"), "local_llm")
        self.assertEqual(self.key("the local model"), "local_llm")
        self.assertEqual(self.key("Ollama"), "local_llm")

    def test_unique_partial_word(self):
        self.assertEqual(self.key("model"), "local_llm")

    def test_ambiguous_reports_candidates(self):
        resolution = self.registry_.resolve("voice")
        self.assertEqual(resolution.key, "")
        self.assertEqual(set(resolution.candidates),
                         {"voice_input", "voice_output"})

    def test_unknown(self):
        resolution = self.registry_.resolve("the lights")
        self.assertEqual((resolution.key, resolution.candidates), ("", ()))
        self.assertEqual(self.registry_.resolve("").key, "")


class PersistenceTests(RegistryTestCase):
    def test_settings_survive_restart(self):
        first = self.registry()
        first.set_profile("standard")
        first.set_enabled("basic", False)

        second = self.registry()
        self.assertEqual(second.profile, "standard")
        self.assertTrue(second.profile_chosen)
        self.assertFalse(second.is_enabled("basic"))

    def test_file_is_plain_json_and_no_temp_files_remain(self):
        registry = self.registry()
        registry.set_enabled("basic", False)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["features"], {"basic": False})
        self.assertEqual([p.name for p in self.path.parent.iterdir()],
                         ["settings.json"])

    def test_corrupt_file_falls_back_and_is_kept(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{ not json", encoding="utf-8")
        registry = self.registry()
        self.assertEqual(registry.profile, "lite")
        self.assertIn("could not be read", registry.settings_error)
        self.assertTrue(Path(str(self.path) + ".corrupt").exists())
        registry.set_enabled("basic", False)             # still usable
        self.assertFalse(self.registry().is_enabled("basic"))

    def test_wrong_shapes_are_ignored_not_fatal(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(
            {"profile": "ultra", "features": {"basic": "yes", "x": True}}),
            encoding="utf-8")
        registry = self.registry()
        self.assertEqual(registry.profile, "lite")
        self.assertTrue(registry.is_enabled("basic"))     # "yes" was dropped
        self.assertEqual(registry.settings_error, "")

    def test_unknown_feature_keys_are_preserved(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(
            {"features": {"from_a_newer_version": True}}), encoding="utf-8")
        registry = self.registry()
        registry.set_enabled("basic", False)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("from_a_newer_version", data["features"])

    def test_failed_save_leaves_the_change_undone(self):
        blocker = Path(self.tmp.name) / "blocker"
        blocker.write_text("i am a file, not a folder")
        registry = FeatureRegistry(blocker / "settings.json",
                                   memory_status=ram(4), features=TEST_FEATURES)
        with self.assertRaises(OSError):
            registry.set_enabled("basic", False)
        self.assertTrue(registry.is_enabled("basic"))
        with self.assertRaises(OSError):
            registry.set_profile("full")
        self.assertEqual(registry.profile, "lite")

    def test_in_memory_registry_touches_no_files(self):
        registry = self.registry(path=False)
        registry.set_enabled("basic", False)
        self.assertFalse(self.path.exists())


class LazyImportTests(RegistryTestCase):
    def test_import_only_when_enabled(self):
        registry = self.registry()
        registry.set_enabled("basic", False)
        with mock.patch("importlib.import_module") as importer:
            with self.assertRaises(FeatureUnavailable):
                registry.import_module("basic", "heavy_library")
            importer.assert_not_called()

        registry.set_enabled("basic", True)
        with mock.patch("importlib.import_module",
                        return_value="module") as importer:
            self.assertEqual(registry.import_module("basic", "heavy_library"),
                             "module")
            importer.assert_called_once_with("heavy_library")

    def test_unavailable_features_cannot_be_imported(self):
        with self.assertRaises(FeatureUnavailable):
            self.registry().import_module("missing_pkg", "anything")


if __name__ == "__main__":
    unittest.main()
