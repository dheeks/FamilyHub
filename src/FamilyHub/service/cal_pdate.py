import threading
import time
from datetime import datetime, date, time as dtime, timedelta
import pytz
import requests
from flask import Flask, jsonify, request
from icalendar import Calendar
from recurring_ical_events import of

app = Flask(__name__)

print("Calendar Sync Service started")

# List of ICS feeds to ingest
# Each feed has:
#   name: label for display
#   color: hex color for UI
#   url: ICS feed URL
CALENDAR_FEEDS = [
    {
        "name": "Name1",
        "color": "#165791",
        "url": "<Enter Google ICS Here>"
    },
    {
        "name": "Name2",
        "color": "#753DE0",
        "url": "<Enter Google ICS Here>"
    },
    {
        "name": "Name3",
        "color": "#DE8104",
        "url": "<Enter Google ICS Here>"
    },
    {
        "name": "Name4",
        "color": "#FF1493",
        "url": "<Enter Google ICS Here>"
    },
    {
        "name": "Name5",
        "color": "#4CB7A5",
        "url": "<Enter Google ICS Here>"
    }
]

# Eastern timezone for all event normalization
EASTERN = pytz.timezone("America/New_York")

# In-memory event store
EVENT_STORE = {
    "events": [],
    "last_updated": None,
    "source_version": 0,
}

# Lock for thread-safe access to EVENT_STORE
STORE_LOCK = threading.Lock()

# How often to re-ingest ICS feeds
INGEST_INTERVAL_SECONDS = 60  # once per minute


def start_ingestion_thread():
    """Start the background ingestion loop in a daemon thread."""
    t = threading.Thread(target=ingestion_loop, daemon=True)
    t.start()


def ingestion_loop():
    """Background loop that periodically fetches and processes ICS feeds."""
    while True:
        try:
            process_calendars()
        except Exception as e:
            print(f"[INGEST] Error during processing: {e}")
        time.sleep(INGEST_INTERVAL_SECONDS)


def process_calendars():
    """Fetch, expand, normalize, prune, and store events from all ICS feeds."""
    global EVENT_STORE

    now = datetime.now(EASTERN)
    today = now.date()

    # Keep events from yesterday forward
    prune_before_date = today - timedelta(days=1)

    # Window for recurrence expansion
    window_start = EASTERN.localize(datetime.combine(prune_before_date, dtime.min))
    window_end = window_start + timedelta(days=60)

    all_events = []

    print(f"[INGEST] Starting calendar fetch at {now.isoformat()}")

    for feed in CALENDAR_FEEDS:
        try:
            ics_data = requests.get(feed["url"], timeout=10).text

            cal = Calendar.from_ical(ics_data)

            # Expand recurring events into concrete instances
            expanded = of(cal).between(window_start, window_end)

            for component in expanded:
                raw_start = component.get("dtstart").dt
                raw_end = component.get("dtend").dt

                # All-day events use date objects
                is_all_day = isinstance(raw_start, date) and not isinstance(raw_start, datetime)

                start = normalize_ical_dt(raw_start, EASTERN)
                end = normalize_ical_dt(raw_end, EASTERN)

                if is_all_day:
                    # Google all-day DTEND is exclusive; subtract 1 microsecond
                    end = end - timedelta(microseconds=1)

                all_events.append({
                    "title": str(component.get("summary")),
                    "start": start,
                    "end": end,
                    "calendar": feed["name"],
                    "color": feed["color"],
                    "all_day": is_all_day,
                })

        except Exception as e:
            print(f"[INGEST] Error fetching/parsing {feed['name']}: {e}")

    # Remove events that ended before prune_before_date
    all_events = [
        e for e in all_events
        if e["end"].date() >= prune_before_date
    ]

    print(f"[INGEST] After prune: {len(all_events)}")

    # Sort by start time
    all_events.sort(key=lambda e: e["start"])

    # Store results atomically
    with STORE_LOCK:
        EVENT_STORE = {
            "events": all_events,
            "last_updated": now,
            "source_version": EVENT_STORE.get("source_version", 0) + 1,
        }

    print(f"[INGEST] Completed. Stored {len(all_events)} events.")


def get_events_in_range(start_dt, end_dt):
    """
    Return events that overlap the half-open interval [start_dt, end_dt).
    """
    with STORE_LOCK:
        events = EVENT_STORE["events"][:]

    result = []
    for e in events:
        ev_start = e["start"]
        ev_end = e["end"]

        # Overlap check: event starts before window end AND ends after window start
        if ev_start < end_dt and ev_end > start_dt:
            result.append(e)

    return result


@app.route("/events/week")
def api_events_week():
    """Return events for the next 7 days."""
    now = datetime.now(EASTERN)
    today = now.date()

    window_start = EASTERN.localize(datetime.combine(today, dtime.min))
    window_end = window_start + timedelta(days=7) - timedelta(microseconds=1)

    events = get_events_in_range(window_start, window_end)

    return jsonify(format_events_for_json(events, window_start, window_end))


@app.route("/events/today")
def api_events_today():
    """Return events for the current day."""
    now = datetime.now(EASTERN)
    today = now.date()

    day_start = EASTERN.localize(datetime.combine(today, dtime.min))
    day_end = day_start + timedelta(days=1)

    events = get_events_in_range(day_start, day_end)

    return jsonify(format_events_for_json(events))


@app.route("/events/range")
def api_events_range():
    """Return events for a user-specified date range."""
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    if not start_str or not end_str:
        return jsonify({"error": "start and end query params required (YYYY-MM-DD)"}), 400

    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Invalid date format, use YYYY-MM-DD"}), 400

    # End date is exclusive
    start_dt = EASTERN.localize(datetime.combine(start_date, dtime.min))
    end_dt = EASTERN.localize(datetime.combine(end_date, dtime.min))

    events = get_events_in_range(start_dt, end_dt)

    return jsonify(format_events_for_json(events))


@app.route("/reload", methods=["POST"])
def api_reload():
    """Force a manual reload of all ICS feeds."""
    try:
        process_calendars()
        return jsonify({"status": "ok", "message": "Reloaded"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/health")
def api_health():
    """Return basic health and status information."""
    with STORE_LOCK:
        last_updated = EVENT_STORE["last_updated"]
        count = len(EVENT_STORE["events"])
        version = EVENT_STORE["source_version"]

    return jsonify({
        "status": "ok",
        "events_count": count,
        "last_updated": last_updated.isoformat() if last_updated else None,
        "source_version": version,
    })


def format_events_for_json(events, window_start=None, window_end=None):
    """Format events into JSON-safe dictionaries for API responses."""
    formatted = []

    for e in events:
        start = e["start"]
        end = e["end"]
        is_all_day = e["all_day"]

        # Clamp to window if provided
        if window_start:
            start = max(start, window_start)
        if window_end:
            end = min(end, window_end)

        # Display time formatting
        if is_all_day:
            display_start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            display_time = "12:00 AM"
        else:
            display_start = start
            display_time = display_start.strftime("%I:%M %p")

        formatted.append({
            "title": e["title"],
            "start": f"{display_start.strftime('%Y-%m-%d')} {display_time}",
            "end": end.strftime("%Y-%m-%d %I:%M %p"),
            "date": display_start.date().isoformat(),
            "calendar": e["calendar"],
            "color": e["color"],
            "all_day": is_all_day,
        })

    return formatted


@app.route("/debug/events")
def api_debug_events():
    """Return raw event data for debugging."""
    with STORE_LOCK:
        events = EVENT_STORE["events"][:]

    debug = []
    for e in events:
        debug.append({
            "title": e["title"],
            "start": e["start"].isoformat(),
            "end": e["end"].isoformat(),
            "calendar": e["calendar"],
            "all_day": e["all_day"],
        })

    return jsonify(debug)


@app.route("/debug/all")
def debug_all():
    """Return all raw events from all feeds without pruning or expansion."""
    events = []

    for feed in CALENDAR_FEEDS:
        try:
            ics_data = requests.get(feed["url"], timeout=10).text
            cal = Calendar.from_ical(ics_data)

            timeline = cal.timeline
            try:
                timeline.include_recurrence = True
            except:
                pass

            for event in timeline:
                events.append({
                    "title": event.name,
                    "start": event.begin.isoformat(),
                    "end": event.end.isoformat(),
                    "calendar": feed["name"],
                })

        except Exception as e:
            print("Error:", e)

    events.sort(key=lambda e: e["start"])

    return jsonify(events)





def normalize_ical_dt(value, tzinfo):
    """
    Convert an icalendar dt (date or datetime) into a timezone-aware datetime.
    """
    if isinstance(value, datetime):
        # If no timezone, localize it
        if value.tzinfo is None:
            return tzinfo.localize(value)
        return value.astimezone(tzinfo)

    if isinstance(value, date):
        # Convert all-day date into midnight local time
        return tzinfo.localize(datetime.combine(value, dtime.min))

    raise TypeError(f"Unsupported dt type: {type(value)}")


if __name__ == "__main__":
    # Start background ingestion thread
    start_ingestion_thread()

    # Run Flask service
    app.run(host="0.0.0.0", port=5001)
