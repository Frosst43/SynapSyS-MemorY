"""SynapSyS-MemorY: memoria modular local para aplicaciones de IA."""

from .store import MemoryStore, MemoryConflictError

__version__ = "1.0.0rc1"
__all__ = ["MemoryStore", "MemoryConflictError"]
