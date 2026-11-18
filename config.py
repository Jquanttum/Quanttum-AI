from pathlib import Path

APP_NAME = "QUANTTUM AI"
APP_VERSION = "0.6.0"

# Anchor the database to the project folder so it is the same file no matter
# which directory the app is launched from.
BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = str(BASE_DIR / "data" / "assistant.db")
SETTINGS_PATH = str(BASE_DIR / "data" / "settings.json")
PERMISSIONS_PATH = str(BASE_DIR / "data" / "permissions.json")
FILE_INDEX_PATH = str(BASE_DIR / "data" / "fileindex.db")

# The local model server. Only this computer is allowed (see core/llm.py).
OLLAMA_HOST = "http://127.0.0.1:11434"

# Put unzipped Vosk speech models in here (they are large, so Git ignores them).
VOSK_MODELS_DIR = str(BASE_DIR / "data" / "models")

MAX_RAM_WARNING_PERCENT = 80
