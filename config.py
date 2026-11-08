from pathlib import Path

APP_NAME = "QUANTTUM AI"
APP_VERSION = "0.1.1"

# Anchor the database to the project folder so it is the same file no matter
# which directory the app is launched from.
BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = str(BASE_DIR / "data" / "assistant.db")

MAX_RAM_WARNING_PERCENT = 80
