"""NYC Housing RAG application package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("nyc-housing-rag")
except PackageNotFoundError:
    __version__ = "0.1.0.dev0"
