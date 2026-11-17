"""The HTTP API. Needs the service extra: ``pip install duebook[service]``."""

from .app import create_app

__all__ = ["create_app"]
