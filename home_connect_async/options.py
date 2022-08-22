"""Represents an Option."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dataclasses_json import Undefined, dataclass_json


@dataclass_json(undefined=Undefined.EXCLUDE)
@dataclass
class Option:
    """Class to represent a Home Connect Option."""

    key: str
    value: Any | None = None
    type: str | None = None
    name: str | None = None
    unit: str | None = None
    displayvalue: str | None = None
    min: int | None = None
    max: int | None = None
    stepsize: int | None = None
    allowedvalues: list[str] | None = None
    execution: str | None = None
    liveupdate: bool | None = None
    access: str | None = None

    @classmethod
    def create(cls, data: dict):
        """A factory to create a new instance from a dictionary in the Home Connect format."""
        option = Option(
            key=data["key"],
            type=data.get("type"),
            name=data.get("name"),
            value=data.get("value"),
            unit=data.get("unit"),
            displayvalue=data.get("displayvalue"),
        )
        if "constraints" in data:
            constraints: dict = data["constraints"]
            option.min = constraints.get("min")
            option.max = constraints.get("max")
            option.stepsize = constraints.get("stepsize")
            option.allowedvalues = constraints.get("allowedvalues")
            option.execution = constraints.get("execution")
            option.liveupdate = constraints.get("liveupdate")
            option.access = constraints.get("access")
        return option

    def get_option_to_apply(self, value, exception_on_error=False):
        """Construct an option dict that can be sent to the Home Connect API."""

        def value_error():
            if exception_on_error:
                raise ValueError(f"Invalid value for this option: {value}")
            else:
                return None

        if self.allowedvalues is not None and value not in self.allowedvalues:
            return value_error()

        if self.min is not None and value < self.min:
            return value_error()

        if self.max is not None and value > self.max:
            return value_error()

        if self.stepsize is not None and value % self.stepsize != 0:
            return value_error()

        return {"key": self.key, "value": self.value, "unit": self.unit}
