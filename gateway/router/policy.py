"""
Load and validate a YAML routing policy.

Policy schema example (see policies/default_policy.yaml):

  tiers:
    simple:
      provider: anthropic
      model: claude-haiku-4-5-20251001
    moderate:
      provider: anthropic
      model: claude-sonnet-4-6
    complex:
      provider: anthropic
      model: claude-sonnet-4-6
    sensitive:
      provider: anthropic
      model: claude-opus-4-6
  overrides: []          # list of {match: {feature_tag: "..."}, tier: "..."}
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class TierConfig(BaseModel):
    provider: str
    model: str


class PolicyOverride(BaseModel):
    match: dict[str, Any]
    tier: str


class Policy(BaseModel):
    tiers: dict[str, TierConfig]
    overrides: list[PolicyOverride] = []


@lru_cache(maxsize=32)
def load_policy(name: str) -> Policy:
    policies_dir = Path(os.getenv("POLICIES_DIR", "policies"))
    path = policies_dir / name
    if not path.exists():
        raise FileNotFoundError(f"Policy file not found: {path}")
    raw = yaml.safe_load(path.read_text())
    return Policy(**raw)


def get_policy(tenant_id: str | None) -> Policy:
    """Return the policy for a tenant, falling back to the default."""
    default = os.getenv("DEFAULT_POLICY", "default_policy.yaml")
    if tenant_id:
        tenant_file = f"{tenant_id}.yaml"
        try:
            return load_policy(tenant_file)
        except FileNotFoundError:
            pass
    return load_policy(default)
