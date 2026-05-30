"""Small configuration helpers for hunter-agent.

The project keeps important settings in `config.toml` at the repo root.
For now we only read the values the web app needs. Later, you can extend this
module to expose scheduler, source, profile, and Ollama settings in a typed way.
"""

from pathlib import Path
import tomllib


# `app/config.py` lives one level below the project root.
# Using Path objects keeps file paths readable and avoids fragile string joins.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.toml"


def load_config() -> dict:
    """Read config.toml and return it as a plain Python dictionary.

    `tomllib` is built into modern Python and is enough for reading TOML.
    TODO: Add validation once you know which settings the app truly depends on.
    """
    with CONFIG_PATH.open("rb") as config_file:
        return tomllib.load(config_file)


def get_database_path() -> Path:
    """Return the SQLite database path from config.toml.

    The config stores a relative path, so we resolve it from the project root.
    This makes the app work the same way whether you run it from a shell,
    Uvicorn, or a test.
    """
    config = load_config()
    db_path = config["agent"]["db_path"]
    return (PROJECT_ROOT / db_path).resolve()
