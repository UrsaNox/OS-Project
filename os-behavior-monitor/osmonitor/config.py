"""Load local configuration without replacing explicitly exported variables."""
from pathlib import Path

from dotenv import load_dotenv


def load_env(path=None):
    env_path = Path(path) if path is not None else Path(__file__).resolve().parents[1] / '.env'
    return load_dotenv(dotenv_path=env_path, override=False)
