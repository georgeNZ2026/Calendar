# Sports calendar

A calendar feed of the sports George follows, replacing Caltrics.

- **Subscribe:** `webcal://georgenz2026.github.io/Calendar/sports.ics`
- **Readable list:** https://georgenz2026.github.io/Calendar/

## How it fits together

| File | What it is |
|---|---|
| `config.json` | Which sports are followed (`enabled`), the alert time (15 minutes) and the calendar name |
| `data/<sport>.json` | The fixtures for each sport, with the source each one came from |
| `build.py` | Turns the data into the feed, and prints what was added, removed or moved |
| `sports.ics` | The generated feed (don't edit by hand) |
| `index.html` | The generated readable list (don't edit by hand) |

Times are stored in the venue's time zone, so events stay correct wherever the
phone is. Events without a published start time are all-day placeholders with
no alert; they become timed events with the 15-minute alert once a time is out.

## Changing what's followed

Set a sport's `enabled` to `false` in `config.json` (or add a new sport and its
`data/<id>.json` file), then preview and build:

```sh
pip install "icalendar>=6"
python3 build.py --dry-run   # show what would be added / removed / moved
python3 build.py             # write sports.ics and index.html
```

The build refuses to write a feed that shrinks by more than 40% in one go
(guarding against a bad data update emptying the calendar); `--force`
overrides it.

## Keeping it up to date

A scheduled Claude task checks every sport's sources twice a week, updates the
data files, rebuilds, commits, and reports what changed.
