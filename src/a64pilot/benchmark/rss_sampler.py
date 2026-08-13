"""Compatibility export for runtime RSS sampling."""

from __future__ import annotations

try:
    from a64pilot.runtime.rss_sampler import RSSSample, RSSSampler
except ImportError:  # pragma: no cover - available after runtime extras are installed
    RSSSample = None  # type: ignore[assignment,misc]
    RSSSampler = None  # type: ignore[assignment,misc]

__all__ = ["RSSSample", "RSSSampler"]
