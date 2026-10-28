"""Local structured storage; no model credentials are stored here."""
from nonocode.storage.database import Database, default_database_path

__all__ = ['Database', 'default_database_path']
