# Quanttum AI — 0.1.1

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
- Explicit long-term memory: `remember that ...`, `what do you remember about ...`, `forget #<id>`
- Versioned database schema (existing data is upgraded in place)
- Basic rule-based intent recognition
- Date and time responses
- Windows physical RAM monitoring
- No cloud calls and no language model required

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
