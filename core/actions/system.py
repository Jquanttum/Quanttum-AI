"""Level 1 action: read-only system information."""

import os
import platform
import shutil
import sys
from pathlib import Path

from core.actions.base import Action, ActionResult, Level


class SystemInfo(Action):
    name = "system_info"
    level = Level.READ_ONLY
    title = "Show system information"

    def run(self, params, env):
        lines = ["System information"]
        lines.append(f"OS: {platform.system()} {platform.release()} "
                     f"({platform.machine()})")
        lines.append(f"Python: {platform.python_version()} "
                     f"({'64' if sys.maxsize > 2**32 else '32'}-bit)")
        lines.append(f"CPU cores: {os.cpu_count()}")

        status = env.memory_status() if env.memory_status else None

        if status:
            lines.append(
                f"RAM: {status['percent']}% used, "
                f"{status['available_gb']:.2f} GB free of "
                f"{status['total_gb']:.2f} GB")
        else:
            lines.append("RAM: not available on this system")

        try:
            anchor = Path.home().anchor or "/"
            usage = shutil.disk_usage(anchor)
            lines.append(
                f"Disk {anchor}: {usage.free / 1024**3:.1f} GB free of "
                f"{usage.total / 1024**3:.1f} GB")
        except OSError:
            lines.append("Disk: not available")

        if env.features is not None:
            lines.append(f"Profile: {env.features.profile}")

        return ActionResult("\n".join(lines))
