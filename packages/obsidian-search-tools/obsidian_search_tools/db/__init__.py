"""Database package for obsidian-search-tools."""

from .connection import connect, get_db_path
from .schema import EMBEDDING_DIM, init_schema

__all__ = ["EMBEDDING_DIM", "connect", "get_db_path", "init_schema"]
