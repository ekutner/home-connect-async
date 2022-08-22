"""Represents a Command."""
from __future__ import annotations

from dataclasses import dataclass

from dataclasses_json import Undefined, dataclass_json


@dataclass_json(undefined=Undefined.EXCLUDE)
@dataclass
class Command:
    """Class to represent a Home Connect Command."""

    key: str
    name: str | None = None

    @classmethod
    def create(cls, data: dict):
        """A factory to create a new instance from a dictionary in the Home Connect format."""
        status = Command(
            key=data["key"],
            name=data.get("name"),
        )
        return status
