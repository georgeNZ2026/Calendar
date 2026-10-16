#!/usr/bin/env python3
"""Build the sports calendar feed.

Reads config.json (which sports are followed) and data/<sport>.json (the
fixtures), then writes:

  sports.ics   - the calendar feed Apple Calendar subscribes to
  index.html   - a readable list of every event in the feed

Before writing, it compares the new feed with the existing sports.ics and
prints what was added, removed or moved. Use --dry-run to see that preview
without writing anything.

Event format (data/<sport>.json -> "events"):
  Timed:   {"id", "title", "start", "tz", "minutes", "location", ...}
           "start" is either local venue time ("2026-11-07T14:10") or an
           absolute time with an offset ("2026-11-07T14:10:00Z").
  All-day: {"id", "title", "date", "end_date"(optional, inclusive), "tz", ...}
           Used as a placeholder until a start time is published.
Optional fields: "status" (confirmed | provisional | placeholder),
"notes", "source", "updated" (ISO date the event last changed).
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from icalendar import Alarm, Calendar, Event

ROOT = Path(__file__).resolve().parent
UK = ZoneInfo("Europe/London")
UTC = dt.timezone.utc
FEED_URL = "https://georgenz2026.github.io/Calendar/sports.ics"
UID_DOMAIN = "georgenz2026.github.io"
DEFAULT_STAMP = dt.datetime(2026, 10, 16, tzinfo=UTC)


class BuildError(Exception):
    pass


# ---------------------------------------------------------------- loading

def load_config() -> dict:
    return json.loads((ROOT / "config.json").read_text())


def load_events(config: dict) -> list[dict]:
    events, seen = [], set()
    for sport in config["sports"]:
        if not sport.get("enabled", True):
            continue
        path = ROOT / "data" / f"{sport['id']}.json"
        if not path.exists():
            raise BuildError(f"{sport['id']}: data file {path.name} is missing")
        for ev in json.loads(path.read_text())["events"]:
            ev = dict(ev, sport=sport["id"], sport_name=sport["name"],
                      emoji=sport.get("emoji", ""))
            validate(ev)
            if ev["id"] in seen:
                raise BuildError(f"duplicate event id {ev['id']}")
            seen.add(ev["id"])
            events.append(ev)
    return events


def validate(ev: dict) -> None:
    where = ev.get("id", "<no id>")
    for field in ("id", "title", "tz"):
        if not ev.get(field):
            raise BuildError(f"{where}: missing '{field}'")
    try:
        ZoneInfo(ev["tz"])
    except Exception as exc:  # noqa: BLE001
        raise BuildError(f"{where}: unknown time zone {ev['tz']!r}") from exc
    if ("start" in ev) == ("date" in ev):
        raise BuildError(f"{where}: needs exactly one of 'start' or 'date'")
    if "start" in ev:
        start_of(ev)
        if int(ev.get("minutes", 0)) <= 0:
            raise BuildError(f"{where}: 'minutes' must be positive")
    else:
        first, last = dates_of(ev)
        if last < first:
            raise BuildError(f"{where}: end_date is before date")


def start_of(ev: dict) -> dt.datetime:
    """Start as an aware datetime in the venue's time zone."""
    tz = ZoneInfo(ev["tz"])
    value = dt.datetime.fromisoformat(ev["start"].replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    return value.astimezone(tz)


def dates_of(ev: dict) -> tuple[dt.date, dt.date]:
    first = dt.date.fromisoformat(ev["date"])
    last = dt.date.fromisoformat(ev.get("end_date", ev["date"]))
    return first, last


def sort_key(ev: dict) -> dt.datetime:
    if "start" in ev:
        return start_of(ev).astimezone(UTC)
    first, _ = dates_of(ev)
    return dt.datetime.combine(first, dt.time(0), ZoneInfo(ev["tz"])).astimezone(UTC)


def end_utc(ev: dict) -> dt.datetime:
    if "start" in ev:
        return (start_of(ev) + dt.timedelta(minutes=int(ev["minutes"]))).astimezone(UTC)
    _, last = dates_of(ev)
    return dt.datetime.combine(last + dt.timedelta(days=1), dt.time(0),
                               ZoneInfo(ev["tz"])).astimezone(UTC)


# ---------------------------------------------------------------- feed

def describe(ev: dict) -> str:
    lines = []
    if "start" in ev:
        local = start_of(ev)
        lines.append(f"Local start: {local:%a %d %b %Y, %H:%M} ({ev['tz']})")
    else:
        lines.append("Start time not announced yet. This becomes a timed "
                     "event with an alert once it is published.")
    status = ev.get("status", "confirmed")
    if status == "provisional":
        lines.append("Provisional: this may still move.")
    if ev.get("notes"):
        lines.append(ev["notes"])
    if ev.get("source"):
        lines.append(f"Source: {ev['source']}")
    return "\n".join(lines)


def build_calendar(config: dict, events: list[dict]) -> Calendar:
    cal = Calendar()
    cal.add("prodid", "-//georgeNZ2026//Sports Calendar//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", config.get("calendar_name", "Sports"))
    cal.add("x-wr-caldesc", "Fixtures George follows, kept up to date.")
    cal.add("refresh-interval", dt.timedelta(hours=6), parameters={"VALUE": "DURATION"})
    cal.add("x-published-ttl", "PT6H")
    alert = int(config.get("alert_minutes", 15))

    for ev in events:
        item = Event()
        item.add("uid", f"{ev['id']}@{UID_DOMAIN}")
        item.add("summary", f"{ev['emoji']} {ev['title']}".strip())
        stamp = (dt.datetime.fromisoformat(ev["updated"]).replace(tzinfo=UTC)
                 if ev.get("updated") else DEFAULT_STAMP)
        item.add("dtstamp", stamp)
        item.add("last-modified", stamp)
        item.add("transp", "TRANSPARENT")
        item.add("categories", [ev["sport_name"]])
        if ev.get("location"):
            item.add("location", ev["location"])
        item.add("description", describe(ev))
        if "start" in ev:
            start = start_of(ev)
            item.add("dtstart", start)
            item.add("dtend", start + dt.timedelta(minutes=int(ev["minutes"])))
            if alert > 0:
                alarm = Alarm()
                alarm.add("action", "DISPLAY")
                alarm.add("description", ev["title"])
                alarm.add("trigger", dt.timedelta(minutes=-alert))
                item.add_component(alarm)
        else:
            first, last = dates_of(ev)
            item.add("dtstart", first)
            item.add("dtend", last + dt.timedelta(days=1))
        cal.add_component(item)

    # Time zone rules only need to cover the span of the feed.
    year = dt.date.today().year
    cal.add_missing_timezones(first_date=dt.date(year - 1, 1, 1),
                              last_date=dt.date(year + 3, 12, 31))
    return cal


# ---------------------------------------------------------------- diff

def snapshot_from_ics(path: Path) -> dict[str, tuple[str, str, str]]:
    """uid -> (summary, start shown in UK time, sortable start) for a feed."""
    if not path.exists():
        return {}
    return snapshot_from_ics_bytes(path.read_bytes())


def snapshot_from_ics_bytes(data: bytes) -> dict[str, tuple[str, str, str]]:
    snap = {}
    for comp in Calendar.from_ical(data).walk("VEVENT"):
        value = comp.decoded("dtstart")
        if isinstance(value, dt.datetime):
            shown = value.astimezone(UK).strftime("%a %d %b %Y %H:%M UK")
            order = value.astimezone(UTC).isoformat()
        else:
            end = comp.decoded("dtend") - dt.timedelta(days=1)
            shown = (value.strftime("%a %d %b %Y")
                     + (f"–{end:%a %d %b}" if end > value else "") + " (all day)")
            order = dt.datetime.combine(value, dt.time(0), UTC).isoformat()
        snap[str(comp["uid"])] = (str(comp["summary"]), shown, order)
    return snap


def diff(old: dict, new: dict) -> list[str]:
    out = []
    for uid in sorted(new.keys() - old.keys(), key=lambda u: new[u][2]):
        out.append(f"+ added   {new[uid][0]} — {new[uid][1]}")
    for uid in sorted(old.keys() - new.keys(), key=lambda u: old[u][2]):
        out.append(f"- removed {old[uid][0]} — {old[uid][1]}")
    for uid in sorted(old.keys() & new.keys(), key=lambda u: new[u][2]):
        (t0, s0, _), (t1, s1, _) = old[uid], new[uid]
        if s0 != s1:
            out.append(f"~ moved   {t1}: {s0} → {s1}")
        elif t0 != t1:
            out.append(f"~ renamed {t0} → {t1}")
    return out


# ---------------------------------------------------------------- page

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sports Calendar</title>
<style>
:root {{ --bg:#f7f7f5; --card:#fff; --ink:#1b1b1b; --muted:#6b6b6b; --line:#e4e4e0; --accent:#0b6e4f; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#141414; --card:#1d1d1d; --ink:#ececec; --muted:#9a9a9a; --line:#2c2c2c; --accent:#4cc38a; }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
main {{ max-width:760px; margin:0 auto; padding:24px 16px 48px; }}
h1 {{ font-size:24px; margin:0 0 4px; }}
.sub {{ color:var(--muted); margin:0 0 16px; }}
.subscribe {{ display:inline-block; background:var(--accent); color:#fff; text-decoration:none;
  padding:10px 16px; border-radius:8px; font-weight:600; }}
.url {{ color:var(--muted); font-size:13px; word-break:break-all; margin:8px 0 24px; }}
h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted);
  margin:28px 0 8px; }}
ul {{ list-style:none; margin:0; padding:0; background:var(--card); border:1px solid var(--line);
  border-radius:10px; overflow:hidden; }}
li {{ display:grid; grid-template-columns:118px 1fr; gap:12px; padding:10px 14px;
  border-top:1px solid var(--line); }}
li:first-child {{ border-top:0; }}
.when {{ font-variant-numeric:tabular-nums; color:var(--muted); font-size:14px; }}
.what {{ font-weight:500; }}
.meta {{ color:var(--muted); font-size:13px; }}
.tag {{ font-size:11px; border:1px solid var(--line); border-radius:4px; padding:0 5px; margin-left:6px; }}
</style>
</head>
<body>
<main>
<h1>Sports Calendar</h1>
<p class="sub">{count} upcoming events · rebuilt {built}. Times show in your device's time zone.</p>
<a class="subscribe" href="{webcal}">Subscribe in Apple Calendar</a>
<p class="url">{feed}</p>
{sections}
</main>
<script>
document.querySelectorAll('[data-utc]').forEach(function (el) {{
  var d = new Date(el.dataset.utc);
  el.textContent = d.toLocaleString(undefined, {{weekday:'short', day:'numeric', month:'short',
    hour:'2-digit', minute:'2-digit'}});
}});
</script>
</body>
</html>
"""


def build_page(events: list[dict], now: dt.datetime) -> str:
    upcoming = [e for e in events if end_utc(e) >= now]
    sections, month, items = [], None, []

    def flush():
        if month and items:
            sections.append(f"<h2>{html.escape(month)}</h2>\n<ul>\n" + "\n".join(items) + "\n</ul>")

    for ev in upcoming:
        key = sort_key(ev).astimezone(UK).strftime("%B %Y")
        if key != month:
            flush()
            month, items = key, []
        if "start" in ev:
            utc = start_of(ev).astimezone(UTC)
            when = (f'<span data-utc="{utc.isoformat()}">'
                    f'{utc.astimezone(UK):%a %d %b, %H:%M}</span>')
        else:
            first, last = dates_of(ev)
            span = f"{first:%a %d %b}" if first == last else f"{first:%d %b}–{last:%d %b}"
            when = f"{span}<br>time TBC"
        tag = ' <span class="tag">provisional</span>' if ev.get("status") == "provisional" else ""
        meta = html.escape(ev.get("location", ""))
        items.append(f'<li><div class="when">{when}</div><div><div class="what">'
                     f'{html.escape((ev["emoji"] + " " + ev["title"]).strip())}{tag}</div>'
                     f'<div class="meta">{meta}</div></div></li>')
    flush()
    return PAGE.format(count=len(upcoming), built=now.astimezone(UK).strftime("%d %b %Y %H:%M UK"),
                       webcal=FEED_URL.replace("https://", "webcal://"), feed=FEED_URL,
                       sections="\n".join(sections))


# ---------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="show changes, write nothing")
    parser.add_argument("--force", action="store_true",
                        help="write even if the feed shrinks sharply")
    args = parser.parse_args()

    now = dt.datetime.now(UTC)
    config = load_config()
    keep_past = dt.timedelta(days=int(config.get("keep_past_days", 60)))
    events = sorted((e for e in load_events(config) if end_utc(e) >= now - keep_past),
                    key=sort_key)

    for sport in config["sports"]:
        if sport.get("enabled", True) and not any(
                e["sport"] == sport["id"] and end_utc(e) >= now for e in events):
            print(f"warning: no upcoming events for {sport['name']}", file=sys.stderr)

    cal = build_calendar(config, events)
    feed = cal.to_ical()
    old = snapshot_from_ics(ROOT / "sports.ics")
    new = snapshot_from_ics_bytes(feed)
    changes = diff(old, new)

    if old and len(new) < 0.6 * len(old) and not args.force:
        raise BuildError(f"feed would shrink from {len(old)} to {len(new)} events; "
                         "check the data or rerun with --force")

    print(f"{len(new)} events in feed ({len(changes)} changes)")
    for line in changes:
        print(line)

    if args.dry_run:
        return 0
    (ROOT / "sports.ics").write_bytes(feed)
    (ROOT / "index.html").write_text(build_page(events, now))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
