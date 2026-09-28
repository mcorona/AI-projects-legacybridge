"""Configuración común de pytest: carga `.env` (si existe) sin sobrescribir el entorno."""
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
