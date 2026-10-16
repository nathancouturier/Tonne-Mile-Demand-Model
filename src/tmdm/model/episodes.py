"""Named market episodes used for the decomposition explorer.

Each episode is a start month and an end month. The model only reports an
episode when both endpoints (including the averaging window) fall inside the
period covered by every input series, so the list shown on the site depends on
the data actually available at build time.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Episode:
    """A named period of the dry bulk market."""

    episode_id: str
    name: str
    start: str  # YYYY-MM
    end: str  # YYYY-MM
    description: str


EPISODES: list[Episode] = [
    Episode(
        "spike_2008",
        "2008 spike and crash",
        "2007-01",
        "2008-12",
        "The index set its all time high in May 2008 and lost more than 90 percent by December 2008.",
    ),
    Episode(
        "glut_2010_2012",
        "Post-crisis fleet glut",
        "2010-01",
        "2012-02",
        (
            "Record newbuilding deliveries ordered before 2008 arrived while demand growth slowed; the index "
            "fell from above 3,000 to below 700."
        ),
    ),
    Episode(
        "slide_2014_2016",
        "Slide to the 2016 trough",
        "2013-12",
        "2016-02",
        (
            "Chinese coal imports fell and the fleet kept growing; the index reached its lowest level on "
            "record in February 2016."
        ),
    ),
    Episode(
        "recovery_2016_2018",
        "Recovery from the trough",
        "2016-02",
        "2018-08",
        "Scrapping, slow fleet growth and a rebound in Chinese imports lifted the index back above 1,500.",
    ),
    Episode(
        "vale_2019",
        "2019 Brumadinho dam collapse",
        "2019-01",
        "2019-09",
        (
            "Brazilian iron ore exports fell after the January 2019 dam collapse, then recovered through the "
            "summer alongside a Capesize rally."
        ),
    ),
    Episode(
        "pandemic_2020",
        "Pandemic trough",
        "2019-12",
        "2020-05",
        "Lockdowns cut industrial activity and the seasonal Brazilian slowdown was unusually deep.",
    ),
    Episode(
        "boom_2021",
        "2021 boom",
        "2020-05",
        "2021-10",
        (
            "Stimulus-driven demand, port congestion and a thin orderbook pushed the index to its highest "
            "level since 2008."
        ),
    ),
    Episode(
        "normalisation_2022_2023",
        "Normalisation after the boom",
        "2021-10",
        "2023-02",
        "Congestion unwound and Chinese demand softened; the index fell back below 1,000.",
    ),
    Episode(
        "chokepoints_2023_2024",
        "Red Sea and Panama Canal disruptions",
        "2023-10",
        "2024-06",
        (
            "Attacks on shipping in the Red Sea diverted traffic around the Cape of Good Hope while drought "
            "restricted Panama Canal transits."
        ),
    ),
    Episode(
        "recent",
        "Latest twelve months",
        "latest-12",
        "latest",
        "The most recent twelve months of data available at build time.",
    ),
]


def resolve_episode(ep: Episode, last_month: pd.Period) -> Episode:
    """Replace the ``latest`` placeholders with concrete months."""
    start, end = ep.start, ep.end
    if end == "latest":
        end = str(last_month)
    if start.startswith("latest-"):
        n = int(start.split("-")[1])
        start = str(last_month - n)
    return Episode(ep.episode_id, ep.name, start, end, ep.description)


def covered_episodes(first_month: pd.Period, last_month: pd.Period, window: int) -> list[Episode]:
    """Return the episodes whose start and end (with averaging window) lie inside coverage."""
    out = []
    for ep in EPISODES:
        r = resolve_episode(ep, last_month)
        s, e = pd.Period(r.start, "M"), pd.Period(r.end, "M")
        if s - (window - 1) >= first_month and e <= last_month and e > s:
            out.append(r)
    return out
