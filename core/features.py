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
from dataclasses import dataclass
from pathlib import Path

from core.resource_monitor import get_memory_status
from core.safejson import load_json, save_json

SETTINGS_VERSION = 1


@dataclass(frozen=True)
class Profile:
    name: str
    min_total_gb: float     # smallest installed RAM this profile is meant for
    summary: str
    min_threads: int = 0    # smallest CPU thread count it is meant for


# Windows reports slightly less than the installed amount (an 8 GB PC shows
# about 7.8 GB), so the thresholds sit a little below the nominal sizes.
#
# RAM alone is not enough: a laptop chip with 4 threads and 16 GB of RAM can
# hold a big model but is too slow to run the heaviest features comfortably.
# So a profile needs both enough RAM and enough CPU threads.
PROFILES = {
    "lite": Profile("lite", 0.0, "small machines (around 4 GB RAM)", 0),
    "standard": Profile("standard", 7.0,
                        "8-16 GB RAM and a 4+ thread CPU", 4),
    "full": Profile("full", 14.0,
                    "16 GB RAM or more and a strong CPU (8+ threads) or GPU", 8),
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
            ALL, aliases=("actions",)),
    Feature("file_index", "File index",
            "A background index of your allowed folders for instant file "
            "search. Off = slower live search.",
            ALL, aliases=("index", "file search index", "fast search")),
    Feature("voice_input", "Voice input",
            "Speech recognition (Vosk, fully offline). The microphone only "
            "opens while you press the Mic button.", frozenset(),
            aliases=("speech recognition", "microphone", "mic"),
            packages=("vosk", "pyaudio")),
    Feature("voice_output", "Voice output",
            "Reads replies aloud with the voice built into Windows.",
            frozenset(), aliases=("text to speech", "tts", "speech",
                                  "read aloud")),
    Feature("voice_autosend", "Voice auto-send",
            "Send what you say without pressing Enter. Never for yes/no "
            "answers or authorisation. Needs voice input.", frozenset(),
            aliases=("autosend", "hands free")),
    Feature("local_llm", "Local AI model",
            "A language model running on this PC through Ollama. Answers "
            "anything that isn't a command.", BIG,
            aliases=("llm", "local model", "ollama", "language model",
                     "ai model"),
            min_total_gb=7.0),
    Feature("llm_commands", "AI command understanding",
            "Lets the local model turn free-form sentences into commands. "
            "Slower: every unrecognised message costs an extra model call. "
            "Needs the local AI model.",
            frozenset(), aliases=("smart commands", "ai commands",
                                  "natural language commands")),
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
                 features=FEATURES, find_spec=importlib.util.find_spec,
                 cpu_threads=os.cpu_count):
        self.settings_path = Path(settings_path) if settings_path else None
        self._memory_status = memory_status
        self._cpu_threads = cpu_threads
        self._find_spec = find_spec
        self._features = {feature.key: feature for feature in features}
        self.settings_error = ""
        self._probes = {}
        self._settings = self._load()

    # -----------------------------------------
    # SETTINGS FILE
    # -----------------------------------------

    @staticmethod
    def _blank():
        return {"version": SETTINGS_VERSION, "profile": None, "features": {},
                "options": {}}

    def _load(self):
        settings = self._blank()

        if self.settings_path is None:
            return settings

        raw, error = load_json(self.settings_path, "settings")

        if error:
            self.settings_error = error
            return settings

        if raw is None:
            return settings

        if not isinstance(raw, dict):
            self.settings_error = (
                "Your settings file is not in the expected format. "
                "I'm using the defaults.")
            return settings

        profile = raw.get("profile")
        if profile in PROFILES:
            settings["profile"] = profile

        overrides = raw.get("features", {})
        if isinstance(overrides, dict):
            settings["features"] = {
                key: value for key, value in overrides.items()
                if isinstance(key, str) and isinstance(value, bool)
            }

        options = raw.get("options", {})
        if isinstance(options, dict):
            settings["options"] = {
                key: value for key, value in options.items()
                if isinstance(key, str)
                and isinstance(value, (str, int, float, bool))
            }

        return settings

    def _save(self):
        if self.settings_path is not None:
            save_json(self.settings_path, self._settings)

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

    def threads(self):
        """Logical CPU threads, or None when unknown."""

        return self._cpu_threads()

    def _fitting_profiles(self, use_cpu=True):
        total = self.total_ram_gb()
        threads = self.threads() if use_cpu else None

        return [
            name for name in PROFILE_ORDER
            if (total is None or PROFILES[name].min_total_gb <= total)
            and (threads is None or PROFILES[name].min_threads <= threads)
        ]

    def recommended_profile(self):
        """The largest profile this PC's RAM *and* CPU support.

        RAM is the hard limit, so with no RAM reading there is no
        recommendation (the CPU alone could overshoot).
        """

        if self.total_ram_gb() is None:
            return None

        return self._fitting_profiles()[-1]

    def recommendation_note(self):
        """Explain when the CPU holds back what the RAM would allow."""

        with_cpu = self._fitting_profiles()
        ram_only = self._fitting_profiles(use_cpu=False)

        if not with_cpu or not ram_only or with_cpu[-1] == ram_only[-1]:
            return ""

        return (f"Your RAM would allow '{ram_only[-1]}', but this CPU has "
                f"only {self.threads()} threads, so '{with_cpu[-1]}' is the "
                "better fit.")

    def profile_warning(self):
        """A caveat when the chosen profile asks more than this PC has."""

        wanted = PROFILES[self.profile]
        total = self.total_ram_gb()
        threads = self.threads()

        if total is not None and total < wanted.min_total_gb:
            return (f"The {self.profile} profile is meant for about "
                    f"{wanted.min_total_gb:g} GB or more; this PC has "
                    f"{total:.1f} GB.")

        if threads is not None and threads < wanted.min_threads:
            return (f"The {self.profile} profile is meant for "
                    f"{wanted.min_threads}+ CPU threads; this PC has "
                    f"{threads}.")

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

    # ---- options (free-form settings such as the chosen model) ----------

    def option(self, key, default=None):
        return self._settings["options"].get(key, default)

    def set_option(self, key, value):
        """Save an option, or clear it with None."""

        def mutate(settings):
            if value is None:
                settings["options"].pop(key, None)
            else:
                settings["options"][key] = value

        self._commit(mutate)

    # ---- runtime probes ---------------------------------------------------

    def set_probe(self, key, probe):
        """Register a check that returns "" when the feature can run, or the
        reason it can't (for example: "Ollama isn't running")."""

        self._features[key]
        self._probes[key] = probe

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

        probe = self._probes.get(key)
        if probe is not None:
            reason = probe()
            if reason:
                return FeatureStatus(feature, "unavailable", reason, "", source)

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
