import threading
import time
from datetime import datetime, date, time as dtime, timedelta
import pytz
import requests
from ics import Calendar
from flask import Flask, jsonify, request
from dateutil import rrule, tz
import re
from icalendar import Calendar
from recurring_ical_events import of
from datetime import datetime, time as dtime, timedelta

app = Flask(__name__)

print ("Calendar Sync Service started")

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

EASTERN = pytz.timezone("America/New_York")

EVENT_STORE = {
    "events": [],
    "last_updated": None,
    "source_version": 0,
}

STORE_LOCK = threading.Lock()

INGEST_INTERVAL_SECONDS = 60  # once per minute

def start_ingestion_thread():
    t = threading.Thread(target=ingestion_loop, daemon=True)
    t.start()

def ingestion_loop():
    while True:
        try:
            process_calendars()
        except Exception as e:
            print(f"[INGEST] Error during processing: {e}")
        time.sleep(INGEST_INTERVAL_SECONDS)


def process_calendars():
    global EVENT_STORE

    now = datetime.now(EASTERN)
    today = now.date()

    prune_before_date = today - timedelta(days=1)
    window_start = EASTERN.localize(datetime.combine(prune_before_date, dtime.min))
    window_end = window_start + timedelta(days=60)

    all_events = []

    print(f"[INGEST] Starting calendar fetch at {now.isoformat()}")

    for feed in CALENDAR_FEEDS:
        try:
            ics_data = requests.get(feed["url"], timeout=10).text

            # Parse ICS using icalendar
            from icalendar import Calendar
            from recurring_ical_events import of

            cal = Calendar.from_ical(ics_data)

            expanded = of(cal).between(window_start, window_end)

            for component in expanded:
                raw_start = component.get('dtstart').dt
                is_all_day = isinstance(raw_start, date) and not isinstance(raw_start, datetime)


                raw_end = component.get('dtend').dt

                start = normalize_ical_dt(raw_start, EASTERN)
                end = normalize_ical_dt(raw_end, EASTERN)

                if is_all_day:
                    # Google all-day DTEND is exclusive → subtract 1 microsecond
                    end = end - timedelta(microseconds=1)


                all_events.append({
                    "title": str(component.get('summary')),
                    "start": start,
                    "end": end,
                    "calendar": feed["name"],
                    "color": feed["color"],
                    "all_day": is_all_day,
                })


        except Exception as e:
            print(f"[INGEST] Error fetching/parsing {feed['name']}: {e}")

    # Prune old events
    all_events = [
        e for e in all_events
        if e["end"].date() >= prune_before_date
    ]

    print(f"[INGEST] After prune: {len(all_events)}")

    # Sort
    all_events.sort(key=lambda e: e["start"])

    # Store
    with STORE_LOCK:
        EVENT_STORE = {
            "events": all_events,
            "last_updated": now,
            "source_version": EVENT_STORE.get("source_version", 0) + 1,
        }

    print(f"[INGEST] Completed. Stored {len(all_events)} events.")


def get_events_in_range(start_dt, end_dt):
    """
    Return events that overlap [start_dt, end_dt) in local time.
    """
    with STORE_LOCK:
        events = EVENT_STORE["events"][:]

    result = []
    for e in events:
        ev_start = e["start"]
        ev_end = e["end"]

        # Overlap check: ev_start < end_dt AND ev_end > start_dt
        if ev_start < end_dt and ev_end > start_dt:
            result.append(e)

    return result


@app.route("/events/week")
def api_events_week():
    now = datetime.now(EASTERN)
    today = now.date()

    window_start = EASTERN.localize(datetime.combine(today, dtime.min))
    window_end = window_start + timedelta(days=7) - timedelta(microseconds=1)


    events = get_events_in_range(window_start, window_end)

    return jsonify(format_events_for_json(events, window_start, window_end))



@app.route("/events/today")
def api_events_today():
    now = datetime.now(EASTERN)
    today = now.date()

    day_start = EASTERN.localize(datetime.combine(today, dtime.min))
    day_end = day_start + timedelta(days=1)

    events = get_events_in_range(day_start, day_end)

    return jsonify(format_events_for_json(events))

@app.route("/events/range")
def api_events_range():
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    if not start_str or not end_str:
        return jsonify({"error": "start and end query params required (YYYY-MM-DD)"}), 400

    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Invalid date format, use YYYY-MM-DD"}), 400

    # Interpret end as exclusive upper bound
    start_dt = EASTERN.localize(datetime.combine(start_date, dtime.min))
    end_dt = EASTERN.localize(datetime.combine(end_date, dtime.min))

    events = get_events_in_range(start_dt, end_dt)

    return jsonify(format_events_for_json(events))

@app.route("/reload", methods=["POST"])
def api_reload():
    try:
        process_calendars()
        return jsonify({"status": "ok", "message": "Reloaded"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/health")
def api_health():
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
    formatted = []

    for e in events:
        start = e["start"]
        end = e["end"]
        is_all_day = e["all_day"]

        # clamp to window if provided
        if window_start:
            start = max(start, window_start)
        if window_end:
            end = min(end, window_end)

        # Display time
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
    events = []

    for feed in CALENDAR_FEEDS:
        try:
            ics_data = requests.get(feed["url"], timeout=10).text
            cal = Calendar(ics_data)

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

    # Sort by start time
    events.sort(key=lambda e: e["start"])

    return jsonify(events)

@app.route("/debug/raw_kristin")
def debug_raw_kristin():
    out = []
    for feed in CALENDAR_FEEDS:
        if feed["name"] != "Kristin":
            continue

        ics_data = requests.get(feed["url"], timeout=10).text
        lines = ics_data.splitlines()

        current = []
        inside = False

        for line in lines:
            if line.startswith("BEGIN:VEVENT"):
                inside = True
                current = [line]
            elif line.startswith("END:VEVENT"):
                current.append(line)
                inside = False
                # Only keep VEVENTs that match the title
                if any("Game Night" in l for l in current):
                    out.append("\n".join(current))
            elif inside:
                current.append(line)

    return {"events": out}


from dateutil import rrule

def normalize_ical_dt(value, tzinfo):
    """
    Convert an icalendar dt (date or datetime) into a timezone-aware datetime.
    """
    if isinstance(value, datetime):
        
        if value.tzinfo is None:
            return tzinfo.localize(value)
        return value.astimezone(tzinfo)

    if isinstance(value, date):
        
        return tzinfo.localize(datetime.combine(value, dtime.min))

    raise TypeError(f"Unsupported dt type: {type(value)}")




if __name__ == "__main__":
    #load_events_from_disk()   # optional
    start_ingestion_thread()  # starts the background loop
    app.run(host="0.0.0.0", port=5001)

