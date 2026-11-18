# Quanttum AI — 0.5.0

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
- **Local AI model (Ollama):** anything that isn't a command is answered by a model running on your own PC, with your relevant memories and recent conversation as context. Replies stream into the window, there is a Stop button, and the window never freezes while the model thinks. See *Local AI model* below.
- **Action Engine** with permission levels, an append-only audit log and undo (see *Safety model* below)
- **Fast file search:** `find exam notes`, `find folder projects`. A background index of your allowed folders answers in milliseconds; before it is ready (or if switched off) a slower live search is used
- **Folder creation:** `create folder Reports in Documents` (asks first, then `undo` removes it if still empty)
- Allowed folders: `show allowed folders`, `allow folder D:\Projects`, `remove allowed folder D:\Projects`
- **Opening applications:** approve an app once (`allow app notepad`), then `open notepad`. Both steps ask for a yes. Only approved apps, and only the exact file you approved, can ever be launched. `show allowed apps`, `remove allowed app notepad`
- `system info`, `index status`, `rebuild index`, `show action log`
- Capability profiles (`lite` / `standard` / `full`) and per-feature toggles: `show features`, `enable ram monitor`, `use profile standard`. Settings are saved in `data/settings.json`. The app recommends a profile from your installed RAM *and* CPU threads (a 4-thread laptop CPU is capped at `standard` even with 16 GB RAM) but never switches on its own.
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
| `core/handlers/` | One small function per capability (`basic.py`, `memory_commands.py`, `settings_commands.py`, `action_commands.py`, `confirmation.py`) plus the registry in `__init__.py` |
| `core/actions/` | **Action Engine** (`engine.py`) and the actions: `system.py`, `folders.py`, `search.py`. Handlers never act directly; they request actions from the engine |
| `core/audit.py` | Append-only audit log (database triggers reject UPDATE/DELETE) |
| `core/llm.py` | Ollama client (standard library only). Loopback-only: it refuses any non-local host |
| `core/llm_brain.py` | Model choice for your hardware, prompts, memory-as-context, strict validation of model-suggested commands, benchmark |
| `core/apps.py` | App catalog (Start Menu + PATH), approved-apps list, launcher (no shell, no arguments) |
| `core/policy.py` | Allowed folders: path resolution, escape protection, protected system folders, Windows known-folder lookup (OneDrive-safe) |
| `core/fileindex.py` | SQLite + FTS5 trigram file-name index, incremental refresh, live-search fallback |
| `core/safejson.py` | Atomic JSON writes and corrupt-file quarantine |
| `core/features.py` | Profiles, feature registry, overrides, requirement checks (RAM, packages), lazy-import gate |
| `core/memory.py` | SQLite storage: conversation history, long-term memories (status, category, provenance), FTS5 search, migrations |
| `core/textutils.py` | Pure text helpers: stopwords, search-term extraction, FTS query building, categorisation |
| `core/resource_monitor.py` | Windows RAM monitoring |
| `tests/` | Standard-library `unittest` suite |

To add a capability: write a handler in `core/handlers/`, add its name to `INTENT_NAMES`, teach `parse_intent()` to produce it, and register it in `build_registry()`. A test fails if an intent has no handler.

## Local AI model
Setup (once): install Ollama, then in a terminal pull a model that suits your PC, for example `ollama pull llama3.2:3b`. In the app:

```
use profile standard      (turns the local model on by default)
show model                (Ollama status, installed models, which one is in use)
benchmark                 (measures tokens/second on this PC)
use model qwen2.5:3b      (pick another installed model)  /  use default model
```

* **Private:** the client only talks to `127.0.0.1`/`localhost`. Your messages never leave the PC.
* **The model can only write text.** It cannot open apps, change files or run commands. Anything it suggests you still type yourself, and every action still passes through the permission gates above.
* **Memories as context:** relevant saved facts are added to the prompt, labelled as information (never instructions).
* **Hardware-aware:** the model is chosen from what you have installed, by profile, and models whose weights would take more than 45% of your RAM are skipped. Replies are length-capped per profile.
* **Optional command understanding** (`enable ai command understanding`, off by default): the model may turn a free-form sentence into one of a short list of *safe* commands (find, open app, create folder, remember, recall, system info, time/date). Its output is validated strictly: it must be valid JSON, use an allowed command and expected fields only, and every piece of text must come from words you actually typed. It can **never** produce yes/no/authorisation, forget, undo, permission or settings changes, and the resulting command still asks for confirmation where it normally would. Each unrecognised message costs an extra model call, so it is slower.
* If Ollama stops or the model is missing, replies say why and fall back to memory search.

## Safety model
Quanttum AI never runs free-form commands. It can only perform fixed, named *actions*, and the Action Engine enforces what each level needs:

| Level | Meaning | What the engine requires |
|---|---|---|
| 1 | Read-only (system info, searching files) | Runs immediately |
| 2 | Reversible (create a folder, allow a folder or app, open an app) | You say **yes**; how to undo it is recorded |
| 3 | Destructive | You type an exact phrase; a plain "yes" is refused |

* File actions only work inside **allowed folders** (default: your Documents, Desktop and Downloads). Paths are fully resolved first, so `..` and symlinks cannot escape. Drive roots, your whole home folder and Windows system folders can never be allowed.
* A confirmed action is **re-checked** just before it runs, in case things changed since you answered.
* Every request, refusal, answer and result is written to the **audit log** (`show action log`). The log cannot be edited or deleted by the app.
* Apps: only Start Menu shortcuts (`.lnk`) and programs (`.exe`) can be approved, never scripts. Launching uses the stored, approved path only, with no command shell and no arguments. Uninstallers are never offered.
* The only removal available is of **empty** folders (used by `undo`); nothing is ever deleted recursively.
* If the permissions file is damaged, **no** folders are allowed until you add them again (it never falls back to defaults).
* The file index stores names, sizes and dates only, never file contents, and never follows symlinks.

Local data files (all in `data/`, all ignored by Git): `assistant.db` (+ `.bak-v*` backups), `settings.json`, `permissions.json` (allowed folders and approved apps), `fileindex.db` (disposable, rebuilt on demand).

## Running the tests
```bat
py -3.13 -m unittest discover -s tests -t . -v
```
Tests use temporary databases and never touch `data/assistant.db`.

An optional test drives the real window (it briefly opens one, so it is off by default):
```powershell
$env:QUANTTUM_GUI_TESTS = "1"
py -3.13 -m unittest tests.test_gui_smoke -v
```

## Adding an optional feature
Heavy features must not cost memory when they are off. Register a `Feature` in `core/features.py` (set its default profiles, `min_total_gb`, required `packages`, and clear `planned` once the code exists), then load its libraries only through `registry.import_module("<key>", "<module>")` at first use.

## Development notes
This is the first foundation, not the completed assistant. The next stages are:
1. Test the application and measure idle RAM use.
2. Improve intent handling and add a reminder service.
3. Add voice input/output as optional modules.
4. Evaluate a small Ollama model only after measuring available memory.

The app deliberately does not load a local language model at startup.
