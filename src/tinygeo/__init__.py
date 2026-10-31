"""Offline geographic context; inspect dataset_info() for dataset provenance."""

from .lookup import Gazetteer, Reference, dataset_info, locate, locate_many

__all__ = ["Gazetteer", "Reference", "dataset_info", "locate", "locate_many"]
