"""SynapSyS-MemorY: memoria modular local para aplicaciones de IA."""

from .store import MemoryStore, MemoryConflictError

__version__ = "0.2.0.dev2"
__all__ = ["MemoryStore", "MemoryConflictError"]
