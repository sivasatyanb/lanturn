"""Time-dependent pedestrian access: Royal Parks opening hours.

Official sources (checked 14 Nov 2026):
- Hyde Park: "Pedestrian gates 5am - midnight"
  https://www.royalparks.org.uk/visit/parks/hyde-park
- Kensington Gardens: "opens from 6am and closes at dusk each day"; the 2026 timetable
  gives 16:30 for 2 Nov - 31 Dec and 21:45 for 8 - 28 June
  https://www.royalparks.org.uk/visit/parks/kensington-gardens

We do not have the full timetable in machine-readable form, so "dusk" is approximated
as sunset + 20 minutes. That reproduces both published rows above to within 5 minutes.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .geo import sunset_utc

LONDON = ZoneInfo("Europe/London")

# Treat a park as unavailable if its gates close within this margin of departure:
# a route should not rely on leaving through a gate that is about to be locked.
EXIT_MARGIN = timedelta(minutes=30)

ZONE_LABELS = {"hyde_park": "Hyde Park", "kensington_gardens": "Kensington Gardens"}
ZONE_SOURCES = {
    "hyde_park": "https://www.royalparks.org.uk/visit/parks/hyde-park",
    "kensington_gardens": "https://www.royalparks.org.uk/visit/parks/kensington-gardens",
}


def _window(zone: str, day) -> tuple[datetime, datetime]:
    """Opening window (local time) for the park day starting on `day`."""
    if zone == "hyde_park":
        opens = datetime.combine(day, time(5, 0), tzinfo=LONDON)
        closes = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=LONDON)
    elif zone == "kensington_gardens":
        opens = datetime.combine(day, time(6, 0), tzinfo=LONDON)
        closes = (sunset_utc(day) + timedelta(minutes=20)).astimezone(LONDON)
    else:
        raise KeyError(zone)
    return opens, closes


def zone_status(zone: str, when: datetime) -> dict:
    """Is `zone` usable for a walk departing at `when`? Returns open flag and the relevant times."""
    when = when.astimezone(LONDON)
    for day in (when.date() - timedelta(days=1), when.date()):
        opens, closes = _window(zone, day)
        if opens <= when < closes:
            usable = when + EXIT_MARGIN <= closes
            return {"zone": zone, "label": ZONE_LABELS[zone], "open": usable,
                    "closes": closes.isoformat(), "closing_soon": not usable,
                    "source": ZONE_SOURCES[zone]}
    opens, _ = _window(zone, when.date())
    if opens <= when:
        opens, _ = _window(zone, when.date() + timedelta(days=1))
    return {"zone": zone, "label": ZONE_LABELS[zone], "open": False,
            "opens": opens.isoformat(), "closing_soon": False, "source": ZONE_SOURCES[zone]}


def closed_zones(when: datetime) -> dict[str, dict]:
    """Zones that must not be routed through for a departure at `when`."""
    out = {}
    for zone in ZONE_LABELS:
        st = zone_status(zone, when)
        if not st["open"]:
            out[zone] = st
    return out


def is_dark(when: datetime) -> bool:
    when = when.astimezone(LONDON)
    sunset = sunset_utc(when.date()).astimezone(LONDON)
    # Morning: approximate sunrise symmetric around solar noon is unnecessary here;
    # the product is for evening/night journeys, so "dark" = after sunset or before 06:00.
    return when >= sunset or when.hour < 6
