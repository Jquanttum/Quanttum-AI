"""Handlers for profiles and feature toggles."""

from core.features import PROFILE_ORDER, PROFILES

_MARKS = {"enabled": "[on] ", "disabled": "[off]", "unavailable": "[n/a]"}


def _ram_line(registry):
    total = registry.total_ram_gb()

    if total is None:
        return "I can't detect this PC's RAM on this system."

    return f"This PC has {total:.1f} GB of RAM."


def _profile_line(registry):
    name = registry.profile
    note = "" if registry.profile_chosen else " (default; you haven't chosen yet)"
    return f"Profile: {name}{note}"


def _recommendation(registry):
    recommended = registry.recommended_profile()

    if recommended is None or recommended == registry.profile:
        return ""

    return (f"Recommended for this PC: {recommended}. "
            f"Say: use profile {recommended}")


def _feature_line(status):
    feature = status.feature
    line = f"{_MARKS[status.state]} {feature.name} ({feature.key})"

    if status.reason:
        line += f" - {status.reason}"

    if status.warning:
        line += f" - warning: {status.warning}"

    return line


def show_features(intent, env):
    registry = env.features
    lines = [_profile_line(registry), _ram_line(registry)]

    recommendation = _recommendation(registry)
    if recommendation:
        lines.append(recommendation)

    warning = registry.profile_warning()
    if warning:
        lines.append(f"Warning: {warning}")

    if registry.settings_error:
        lines.append(registry.settings_error)

    lines.append("")
    lines.extend(_feature_line(status) for status in registry.statuses())
    lines.append("")
    lines.append(
        "Say: enable <feature>, disable <feature>, reset feature <feature>, "
        "or use profile <lite|standard|full>."
    )

    return "\n".join(lines)


def show_profile(intent, env):
    registry = env.features
    lines = [_profile_line(registry), _ram_line(registry)]

    recommendation = _recommendation(registry)
    if recommendation:
        lines.append(recommendation)

    warning = registry.profile_warning()
    if warning:
        lines.append(f"Warning: {warning}")

    lines.append("")
    lines.append("Profiles:")

    for name in PROFILE_ORDER:
        marker = "->" if name == registry.profile else "  "
        lines.append(f"{marker} {name}: {PROFILES[name].summary}")

    lines.append("")
    lines.append("A profile only sets defaults. You can still turn any "
                 "single feature on or off (see: show features).")

    return "\n".join(lines)


def set_profile(intent, env):
    registry = env.features
    name = intent.entities["profile"]
    before = {s.feature.key: s.state for s in registry.statuses()}

    try:
        registry.set_profile(name)
    except OSError:
        return "I couldn't save your settings, so the profile was not changed."

    changes = [
        _feature_line(status) for status in registry.statuses()
        if before[status.feature.key] != status.state
    ]

    lines = [f"Profile set to {name} ({PROFILES[name].summary})."]

    warning = registry.profile_warning()
    if warning:
        lines.append(f"Warning: {warning}")

    if changes:
        lines.append("")
        lines.append("What changed:")
        lines.extend(changes)
    else:
        lines.append("None of your features changed state.")

    return "\n".join(lines)


def set_feature(intent, env):
    registry = env.features
    wanted = intent.entities["enabled"]          # True / False / None (reset)
    resolution = registry.resolve(intent.entities["feature"])

    if len(resolution.candidates) > 1:
        options = ", ".join(
            f"{registry.feature(key).name} ({key})"
            for key in resolution.candidates)
        return (f"'{intent.entities['feature']}' could mean: {options}. "
                "Please say which one.")

    if not resolution.key:
        keys = ", ".join(feature.key for feature in registry.features)
        return (f"I don't have a feature called "
                f"'{intent.entities['feature']}'. My features are: {keys}.")

    key = resolution.key
    feature = registry.feature(key)

    try:
        registry.set_enabled(key, wanted)
    except OSError:
        return "I couldn't save your settings, so nothing was changed."

    status = registry.status(key)

    if wanted is None:
        head = (f"{feature.name} is back to the {registry.profile} "
                f"profile default.")
    elif wanted:
        head = f"{feature.name}: you've turned it on."
    else:
        head = f"{feature.name}: you've turned it off."

    if status.state == "enabled":
        tail = "It is now ON."
    elif status.state == "disabled":
        tail = "It is now OFF."
    else:
        tail = f"It can't run yet: {status.reason}."
        if wanted:
            tail += " Your choice is saved and will apply once that's resolved."

    lines = [head, tail]

    if status.warning:
        lines.append(f"Warning: {status.warning}.")

    if wanted is not None:
        opposite = "disable" if wanted else "enable"
        lines.append(f"To undo: {opposite} {key}")

    return "\n".join(lines)
