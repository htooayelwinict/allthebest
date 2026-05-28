"""Planner runtime that selects one strategy and emits a plan."""

from __future__ import annotations

from app.planner.selector import PlannerSelector
from app.schemas import Envelope, Plan


class PlannerRuntime:
    def __init__(self, selector: PlannerSelector | None = None) -> None:
        self._selector = selector or PlannerSelector()

    def run(self, envelope: Envelope) -> Plan:
        planner = self._selector.select(envelope)
        return planner.create_plan(envelope)
