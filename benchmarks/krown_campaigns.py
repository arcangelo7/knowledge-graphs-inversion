# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from dataclasses import dataclass
from typing import Literal, cast

from benchmarks.souffle_inversion import SouffleMode

InversionEngine = Literal["kgi", "souffle"]

SOUFFLE_MODE_LABELS: dict[SouffleMode, str] = {
    "rdf": "RDF-only",
    "provenance": "provenance-aware",
    "hybrid": "hybrid",
}


@dataclass(frozen=True)
class Campaign:
    name: str
    inversion_engine: InversionEngine
    souffle_mode: SouffleMode

    @property
    def label(self) -> str:
        if self.inversion_engine == "souffle":
            return f"Datalog-based ({SOUFFLE_MODE_LABELS[self.souffle_mode]})"
        return "SPARQL-based"


@dataclass(frozen=True)
class Phase:
    """One Soufflé forward execution and the inversions that consume its output."""

    souffle_mode: SouffleMode
    campaigns: tuple[Campaign, ...]


PHASES: tuple[Phase, ...] = (
    Phase(
        "rdf",
        (
            Campaign("sparql", "kgi", "rdf"),
            Campaign("souffle_rdf", "souffle", "rdf"),
        ),
    ),
    Phase("provenance", (Campaign("souffle_provenance", "souffle", "provenance"),)),
    Phase("hybrid", (Campaign("souffle_hybrid", "souffle", "hybrid"),)),
)
CAMPAIGNS: tuple[Campaign, ...] = tuple(
    campaign for phase in PHASES for campaign in phase.campaigns
)


def campaign_payloads(data: dict[str, object]) -> dict[str, dict[str, object]]:
    """Split a results file into one self-contained payload per campaign."""
    shared = {key: value for key, value in data.items() if key != "campaigns"}
    campaigns = cast(dict[str, dict[str, object]], data["campaigns"])
    return {name: {**shared, **campaign} for name, campaign in campaigns.items()}
