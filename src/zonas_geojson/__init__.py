"""Junção local e exata de pontos com zonas GeoJSON."""

__version__ = "1.0.0"

from .core import JoinResult, analyze
from .errors import ZoneError
from .reports import analyze_json

__all__ = ["JoinResult", "ZoneError", "analyze", "analyze_json", "__version__"]
