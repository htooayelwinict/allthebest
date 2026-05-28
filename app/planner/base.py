"""Planner abstractions."""

from __future__ import annotations

from typing import Protocol

from app.schemas import Envelope, Plan


class BasePlanner(Protocol):
    planner_name: str

    def create_plan(self, envelope: Envelope) -> Plan:
        """Create a concrete execution plan from an envelope."""
