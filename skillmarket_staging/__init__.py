"""Flexible parser receipt without a final vacancy/skills application model."""

from .db import initialize, inspect_database
from .ingest import load_skills, receive_batch

__all__ = ["initialize", "inspect_database", "load_skills", "receive_batch"]
