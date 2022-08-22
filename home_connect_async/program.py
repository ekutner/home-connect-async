"""Represents a Program."""
from __future__ import annotations

from dataclasses import dataclass

from dataclasses_json import dataclass_json
from .options import Option

@dataclass_json
@dataclass
class Program:
    """Class to represent a Home Connect Program."""

    key: str
    name: str | None = None
    options: dict[str, Option] | None = None
    execution: str | None = None
    active: bool | None= False

    @classmethod
    def create(cls, data: dict):
        """A factory to create a new instance from a dict in the Home Connect format."""
        program = cls(data["key"])
        program._update(data)
        return program

    def _update(self, data: dict):
        self.key = data["key"]
        self.name = data.get("name")
        if "constraints" in data:
            constraints: dict = data["constraints"]
            self.execution = constraints.get("execution")
        if "options" in data:
            self.options = {}
            for opt in data["options"]:
                o = Option.create(opt)
                self.options[o.key] = o
        return self
