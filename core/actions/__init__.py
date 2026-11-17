"""The Action Engine and the actions Quanttum AI can perform."""

from core.actions.base import (
    Action, ActionError, ActionResult, Level, Outcome,
)
from core.actions.engine import ActionEngine
from core.actions.folders import (
    AllowFolder, CreateFolder, DisallowFolder, RemoveEmptyFolder,
)
from core.actions.apps import AllowApp, DisallowApp, OpenApp
from core.actions.search import FindFiles, RebuildIndex
from core.actions.system import SystemInfo


def default_actions():
    return [SystemInfo(), FindFiles(), RebuildIndex(), AllowFolder(),
            DisallowFolder(), CreateFolder(), RemoveEmptyFolder(),
            AllowApp(), DisallowApp(), OpenApp()]
