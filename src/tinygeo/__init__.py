"""Offline, approximate geographic context; bundled data is currently synthetic."""

from .lookup import Gazetteer, Reference, locate, locate_many

__all__ = ["Gazetteer", "Reference", "locate", "locate_many"]
