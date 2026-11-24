"""RoleBase: Backward-compatibility facade for the Argus engine.

All active development and core modules reside in the `argus` package.
This module re-exports all public symbols for backwards compatibility.
"""
from argus import *
from argus import __all__, __version__
