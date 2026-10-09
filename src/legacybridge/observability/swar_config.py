"""Loads config/swar.yaml (ADR-012): the SWAR weights, thresholds and checkpoints."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "swar.yaml"
PHASES = ("trajectory", "writer", "final")  # in the order they become known


class SignalConfig(BaseModel):
    family: Literal["result", "trajectory", "semantics"]
    weight: float = Field(ge=0.0, le=1.0)
    available_at: Literal["trajectory", "writer", "final"]
    group: str | None = None


class SwarConfig(BaseModel):
    version: int
    thresholds: dict[str, float]
    early_warning: dict[str, float]
    groups: dict[str, dict[str, float]] = Field(default_factory=dict)
    trajectory: dict[str, int]
    signals: dict[str, SignalConfig]

    @model_validator(mode="after")
    def _consistent(self) -> SwarConfig:
        t = self.thresholds
        if not 0 < t["elevated"] < t["high"] <= 1:
            raise ValueError("thresholds must satisfy 0 < elevated < high <= 1")
        for name, s in self.signals.items():
            if s.group and s.group not in self.groups:
                raise ValueError(f"signal {name} names an unknown group {s.group}")
        return self

    @property
    def action_threshold(self) -> float:
        return float(self.early_warning["action_threshold"])

    @property
    def checkpoint_k(self) -> int:
        return int(self.early_warning["checkpoint_tool_results"])

    def level(self, risk: float) -> Literal["low", "elevated", "high"]:
        if risk >= self.thresholds["high"]:
            return "high"
        return "elevated" if risk >= self.thresholds["elevated"] else "low"


@lru_cache(maxsize=1)
def load_swar_config(path: Path = CONFIG_PATH) -> SwarConfig:
    return SwarConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
