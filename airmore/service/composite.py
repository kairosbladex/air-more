"""Separate the versioned internal survey index from formal evaluation and ranking."""
from __future__ import annotations

from typing import Protocol


class CompositeRule(Protocol):
    version: str
    def evaluate(self, statistics: dict) -> dict:
        """Return business-approved components/ranks with evidence and period bindings."""


class UnconfiguredComposite:
    version = "unconfigured/v1"

    def evaluate(self, statistics: dict) -> dict:
        return {"version": self.version, "available": False,
                "reason": "综合指数、排名及贡献率的业务口径待补齐",
                "hangars": {hid: {"current": None, "previous": None, "last_day": None}
                            for hid in statistics["hangars"]}}


class SurveyRelativeComposite:
    version = "survey-relative/v1"

    def evaluate(self, statistics):
        hangars = {hid: {"current": h["relative"]["current"],
                          "previous": h["relative"]["previous"], "last_day": None,
                          "details": h["relative"]}
                   for hid, h in statistics["hangars"].items()}
        return {"version": self.version, "name": "颗粒物巡测相对指数",
                "available": any(h["current"] for h in hangars.values()), "hangars": hangars}
