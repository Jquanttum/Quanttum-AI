"""Capability profiles and the feature registry.

Quanttum AI is built to use a lot of capability (local language models,
voice, semantic search ...) but must also run on a small machine. This module
is the single place that decides which optional features are ON.

Three ideas:

* A PROFILE (lite / standard / full) is a preset: which features are on by
  default for a class of machine.
* A user OVERRIDE (stored in settings.json) turns any single feature on or
  off regardless of profile.
* A feature that is wanted can still be UNAVAILABLE: not built yet, a Python
  package is missing, or the PC has too little RAM. The registry says why
  instead of crashing.

Heavy code must be loaded lazily: ask the registry first, import afterwards
(see FeatureRegistry.import_module), so a disabled feature costs no memory.
"""

import importlib
import importlib.util
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.resource_monitor import get_memory_status

SETTINGS_VERSION = 1


@dataclass(frozen=True)
class Profile:
    name: str
    min_total_gb: float     # smallest installed RAM this profile is meant for
    summary: str


# Windows reports slightly less than the installed amount (an 8 GB PC shows
# about 7.8 GB), so the thresholds sit a little below the nominal sizes.
PROFILES = {
    "lite": Profile("lite", 0.0, "small machines (around 4 GB RAM)"),
    "standard": Profile("standard", 7.0, "around 8 GB RAM"),
    "full": Profile("full", 14.0, "16 GB RAM or more"),
}
PROFILE_ORDER = ("lite", "standard", "full")
DEFAULT_PROFILE = "lite"      # the safe choice until the user picks one

ALL = frozenset(PROFILE_ORDER)
BIG = frozenset({"standard", "full"})


@dataclass(frozen=True)
class Feature:
    key: str
    name: str
    description: str
    profiles: frozenset              # profiles where it is ON by default
    aliases: tuple = ()
    min_total_gb: float = 0.0        # installed RAM it needs to run well
    packages: tuple = ()             # Python packages it imports
    planned: str = ""                # version it arrives in; "" = built


# A feature stays marked as planned until its code exists, so the settings
# screen never offers a switch that does nothing.
FEATURES = (
    Feature("ram_monitor", "RAM monitor",
            "Live memory usage shown at the bottom of the window.",
            ALL, aliases=("ram", "memory monitor", "resource monitor")),
    Feature("action_engine", "Action engine",
            "Permission-controlled computer actions with an audit log.",
            ALL, aliases=("actions",), planned="0.3"),
    Feature("voice_input", "Voice input",
            "Speech recognition.", BIG,
            aliases=("speech recognition", "microphone"), planned="0.4"),
    Feature("voice_output", "Voice output",
            "Spoken replies (text-to-speech).", ALL,
            aliases=("text to speech", "tts", "speech"), planned="0.4"),
    Feature("local_llm", "Local AI model",
            "A language model running on this PC through Ollama.", BIG,
            aliases=("llm", "local model", "ollama", "language model"),
            min_total_gb=7.0, planned="0.5"),
    Feature("semantic_memory", "Semantic memory",
            "Meaning-based memory search using embeddings.", BIG,
            aliases=("embeddings", "semantic search"),
            min_total_gb=7.0, planned="0.5"),
    Feature("automation", "Workflow automation",
            "Reminders and repeatable workflows.", ALL,
            aliases=("workflows", "reminders", "scheduler"), planned="0.6"),
    Feature("documents", "Document understanding",
            "Reading and searching your own documents.", ALL,
            aliases=("document search", "knowledge base", "pdf"),
            planned="0.7"),
    Feature("system_intelligence", "System intelligence",
            "Process insight and performance alerts.", BIG,
            aliases=("system monitoring", "alerts"), planned="0.8"),
)


class FeatureUnavailable(RuntimeError):
    """Raised when code tries to use a feature that is not enabled."""


@dataclass(frozen=True)
class FeatureStatus:
    feature: Feature
    state: str                  # "enabled" | "disabled" | "unavailable"
    reason: str = ""            # why it is disabled / unavailable
    warning: str = ""           # enabled, but with a caveat
    source: str = "profile"     # "profile" or "you" (an explicit override)

    @property
    def enabled(self):
        return self.state == "enabled"


@dataclass(frozen=True)
class Resolution:
    key: str = ""               # the feature, if exactly one matched
    candidates: tuple = ()      # all matches (several = ambiguous)


def _words(text):
    return text.casefold().replace("_", " ").replace("-", " ").split()


class FeatureRegistry:
    """Profile + overrides + requirement checks, saved to settings.json.

    settings_path=None keeps everything in memory (used by tests and when no
    file is wanted). memory_status and find_spec are injectable so the
    registry can be tested without real hardware or packages.
    """

    def __init__(self, settings_path=None, memory_status=get_memory_status,
                 features=FEATURES, find_spec=importlib.util.find_spec):
        self.settings_path = Path(settings_path) if settings_path else None
        self._memory_status = memory_status
        self._find_spec = find_spec
        self._features = {feature.key: feature for feature in features}
        self.settings_error = ""
        self._settings = self._load()

    # -----------------------------------------
    # SETTINGS FILE
    # -----------------------------------------

    @staticmethod
    def _blank():
        return {"version": SETTINGS_VERSION, "profile": None, "features": {}}

    def _load(self):
        settings = self._blank()

        if self.settings_path is None or not self.settings_path.exists():
            return settings

        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))

            if not isinstance(raw, dict):
                raise ValueError("settings file is not a JSON object")

            profile = raw.get("profile")
            if profile in PROFILES:
                settings["profile"] = profile

            overrides = raw.get("features", {})
            if isinstance(overrides, dict):
                settings["features"] = {
                    key: value for key, value in overrides.items()
                    if isinstance(key, str) and isinstance(value, bool)
                }

            return settings

        except (OSError, ValueError) as error:
            # Keep the unreadable file for inspection and start from defaults.
            try:
                os.replace(self.settings_path,
                           str(self.settings_path) + ".corrupt")
                kept = " It was kept as settings.json.corrupt."
            except OSError:
                kept = ""

            self.settings_error = (
                f"Your settings file could not be read ({error}). "
                f"I'm using the defaults.{kept}"
            )
            return self._blank()

    def _save(self):
        if self.settings_path is None:
            return

        self.settings_path.parent.mkdir(parents=True, exist_ok=True)

        # Write to a temporary file, then swap it in, so a crash can never
        # leave a half-written settings file.
        handle, temp_name = tempfile.mkstemp(
            dir=self.settings_path.parent, suffix=".tmp")

        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(self._settings, file, indent=2, sort_keys=True)
            os.replace(temp_name, self.settings_path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def _commit(self, mutate):
        """Apply a change and save it; undo the change if saving fails."""

        snapshot = json.loads(json.dumps(self._settings))

        try:
            mutate(self._settings)
            self._save()
        except BaseException:
            self._settings = snapshot
            raise

    # -----------------------------------------
    # PROFILES
    # -----------------------------------------

    @property
    def profile(self):
        return self._settings["profile"] or DEFAULT_PROFILE

    @property
    def profile_chosen(self):
        return self._settings["profile"] is not None

    def set_profile(self, name):
        name = name.casefold()

        if name not in PROFILES:
            raise ValueError(f"Unknown profile '{name}'.")

        self._commit(lambda settings: settings.update(profile=name))

    def total_ram_gb(self):
        """Installed RAM in GB, or None when it cannot be detected."""

        status = self._memory_status()
        return status["total_gb"] if status else None

    def recommended_profile(self):
        """The largest profile this PC's RAM supports (None if unknown)."""

        total = self.total_ram_gb()

        if total is None:
            return None

        fitting = [name for name in PROFILE_ORDER
                   if PROFILES[name].min_total_gb <= total]
        return fitting[-1]

    def profile_warning(self):
        """A caveat when the chosen profile asks more than this PC has."""

        total = self.total_ram_gb()
        needed = PROFILES[self.profile].min_total_gb

        if total is not None and total < needed:
            return (f"The {self.profile} profile is meant for about "
                    f"{needed:g} GB or more; this PC has {total:.1f} GB.")

        return ""

    # -----------------------------------------
    # FEATURES
    # -----------------------------------------

    @property
    def features(self):
        return tuple(self._features.values())

    def feature(self, key):
        return self._features[key]

    def resolve(self, text):
        """Match what the user typed to a feature.

        Exact key, name or alias wins. Otherwise every word typed must appear
        in a feature's key/name/aliases; one match is accepted, several are
        reported as ambiguous.
        """

        wanted = " ".join(_words(text))
        wanted = wanted[4:] if wanted.startswith("the ") else wanted

        if not wanted:
            return Resolution()

        for feature in self._features.values():
            names = {" ".join(_words(feature.key)),
                     " ".join(_words(feature.name)),
                     *(" ".join(_words(alias)) for alias in feature.aliases)}
            if wanted in names:
                return Resolution(feature.key, (feature.key,))

        typed = set(wanted.split())
        matches = []

        for feature in self._features.values():
            vocabulary = set(_words(feature.key)) | set(_words(feature.name))
            for alias in feature.aliases:
                vocabulary |= set(_words(alias))
            if typed <= vocabulary:
                matches.append(feature.key)

        if len(matches) == 1:
            return Resolution(matches[0], tuple(matches))

        return Resolution("", tuple(matches))

    def override(self, key):
        """True / False if the user chose, None if following the profile."""

        self._features[key]                      # KeyError for unknown keys
        return self._settings["features"].get(key)

    def set_enabled(self, key, value):
        """Turn a feature on (True), off (False) or back to the profile
        default (None). Saved immediately."""

        self._features[key]

        def mutate(settings):
            if value is None:
                settings["features"].pop(key, None)
            else:
                settings["features"][key] = bool(value)

        self._commit(mutate)

    def status(self, key):
        feature = self._features[key]
        override = self._settings["features"].get(key)
        source = "you" if override is not None else "profile"

        if feature.planned:
            return FeatureStatus(
                feature, "unavailable",
                f"not built yet (planned for v{feature.planned})", "", source)

        wanted = (override if override is not None
                  else self.profile in feature.profiles)

        if not wanted:
            reason = ("turned off by you" if override is False
                      else f"off in the {self.profile} profile")
            return FeatureStatus(feature, "disabled", reason, "", source)

        missing = [package for package in feature.packages
                   if self._find_spec(package) is None]

        if missing:
            return FeatureStatus(
                feature, "unavailable",
                "needs Python package: " + ", ".join(missing), "", source)

        warning = ""

        if feature.min_total_gb:
            total = self.total_ram_gb()

            if total is not None and total < feature.min_total_gb:
                shortfall = (f"needs about {feature.min_total_gb:g} GB RAM; "
                             f"this PC has {total:.1f} GB")

                if override is True:
                    warning = shortfall       # the user insisted: allow it
                else:
                    return FeatureStatus(
                        feature, "unavailable", shortfall, "", source)

        return FeatureStatus(feature, "enabled", "", warning, source)

    def statuses(self):
        return [self.status(key) for key in self._features]

    def is_enabled(self, key):
        return self.status(key).enabled

    def import_module(self, key, module_name):
        """Import a module only if the feature is enabled.

        The lazy-loading gate: heavy libraries are imported here, on first
        use, never at start-up and never for a disabled feature.
        """

        status = self.status(key)

        if not status.enabled:
            raise FeatureUnavailable(
                f"{status.feature.name} is {status.state}: {status.reason}")

        return importlib.import_module(module_name)
