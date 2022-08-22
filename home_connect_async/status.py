"""Represents a Status."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dataclasses_json import Undefined, dataclass_json


@dataclass_json(undefined=Undefined.EXCLUDE)
@dataclass
class Status:
    """Class to represent a Home Connect Status."""

    key: str
    value: Any | None = None
    name: str | None = None
    displayvalue: str | None = None
    unit: str | None = None

    @classmethod
    def create(cls, data: dict):
        """A factory to create a new instance from a dictionary in the Home Connect format."""
        status = Status(
            key=data["key"],
            name=data.get("name"),
            value=data.get("value"),
            displayvalue=data.get("displayvalue"),
            unit=data.get("unit"),
        )
        return status
