"""Dataset-side validation orchestration built on top of the minimal Harness."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping

from .core import EpisodeSpec, Policy, RunArtifact


RunOne = Callable[[EpisodeSpec, Policy], RunArtifact]
PolicyFactory = Callable[[], Policy]
ScoreTrace = Callable[[RunArtifact], float]


@dataclass(frozen=True)
class ValidationEvidence:
    runs: Mapping[str, RunArtifact]
    scores: Mapping[str, float]


class EpisodeValidationSuite:
    """Runs construction-time diagnostics; it is intentionally not a Harness."""

    def __init__(self, run_one: RunOne, score_trace: ScoreTrace):
        self._run_one = run_one
        self._score_trace = score_trace

    def collect(
        self,
        episode: EpisodeSpec,
        *,
        policies: Mapping[str, PolicyFactory],
        query_variants: Mapping[str, dict[str, Any]] | None = None,
    ) -> ValidationEvidence:
        runs: dict[str, RunArtifact] = {}
        for name, factory in policies.items():
            runs[name] = self._run_one(episode, factory())
        for name, query in (query_variants or {}).items():
            if not isinstance(query, dict) or set(query) - {"text", "language", "context"}:
                raise ValueError("query diagnostics require a complete public Query DTO")
            bootstrap = deepcopy(episode.public_bootstrap)
            bootstrap["query"] = deepcopy(query)
            variant = replace(episode, public_bootstrap=bootstrap)
            runs[f"query:{name}"] = self._run_one(variant, policies["agent"]())
        return ValidationEvidence(runs=runs, scores={name: self._score_trace(run) for name, run in runs.items()})
