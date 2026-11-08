# Quanttum AI — 0.2.0

A lightweight, offline-first Windows desktop assistant starter.

## Requirements
- Windows 10 64-bit
- Python 3.13
- Internet access for the one-time dependency installation

## First-time setup
1. Extract this folder.
2. Double-click `setup.bat` (installs CustomTkinter).
3. When installation finishes, double-click `run.bat`.

Alternatively, open Command Prompt in this folder and run:

```bat
py -3.13 -m pip install -r requirements.txt
py -3.13 main.py
```

After setup, `run.bat` launches the app without trying to install packages again.

## Current features
- CustomTkinter desktop chat interface
- SQLite conversation persistence
- Intent router: messages become structured `Intent` objects and are dispatched to small handlers
- Short-term context: `forget that` works right after a memory is saved or shown
- Long-term memory with ranked full-text search (SQLite FTS5, stemming, BM25): `remember that ...`, `what do you remember about ...`, `forget #<id>`
- Tasks kept apart from facts: `remember to ...`, `show my tasks`, `show my preferences` (no reminders or alerts yet)
- Updating facts: a similar new fact triggers "replace / keep both / no?" instead of a silent duplicate; replaced facts stay in history
- Related facts: `what's related to #3?`
- Versioned database schema; existing data is upgraded in place, in one transaction, after an automatic backup (`data/assistant.db.bak-v<old version>`)
- Basic rule-based intent recognition
- Date and time responses
- Windows physical RAM monitoring
- No cloud calls and no language model required

## Project structure
| Path | Responsibility |
|---|---|
| `main.py`, `interface/window.py` | Start the app; CustomTkinter chat window |
| `core/assistant.py` | **Core**: receives text, parses the intent, routes to a handler, saves the exchange |
| `core/intent.py` | `Intent` object and rule-based `parse_intent()` |
| `core/context.py` | Short-term conversation state and the `Environment` handlers receive |
| `core/handlers/` | One small function per capability (`basic.py`, `memory_commands.py`, `confirmation.py`) plus the registry in `__init__.py` |
| `core/memory.py` | SQLite storage: conversation history, long-term memories (status, category, provenance), FTS5 search, migrations |
| `core/textutils.py` | Pure text helpers: stopwords, search-term extraction, FTS query building, categorisation |
| `core/resource_monitor.py` | Windows RAM monitoring |
| `tests/` | Standard-library `unittest` suite |

To add a capability: write a handler in `core/handlers/`, add its name to `INTENT_NAMES`, teach `parse_intent()` to produce it, and register it in `build_registry()`. A test fails if an intent has no handler.

## Running the tests
```bat
py -3.13 -m unittest discover -s tests -t . -v
```
Tests use temporary databases and never touch `data/assistant.db`.

## Development notes
This is the first foundation, not the completed assistant. The next stages are:
1. Test the application and measure idle RAM use.
2. Improve intent handling and add a reminder service.
3. Add voice input/output as optional modules.
4. Evaluate a small Ollama model only after measuring available memory.

The app deliberately does not load a local language model at startup.
