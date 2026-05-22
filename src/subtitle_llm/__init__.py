"""Subtitle LLM application package."""

import logging

_package_logger = logging.getLogger(__name__)
_package_logger.addHandler(logging.NullHandler())
_package_logger.propagate = False

__all__ = ["__version__"]

__version__ = "0.2.0"
