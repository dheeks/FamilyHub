
import requests
import os
from ics import Calendar
from datetime import datetime, date, timedelta, time
from flask import Flask, render_template, jsonify, request
import pytz
from google.oauth2 import service_account
from googleapiclient.discovery import build
from threading import Thread
import csv


PHOTO_FOLDERS = {
    "default": "<key folder 1>",
    "school":  "<key folder 2>",
    "vacation": "<key folder 3>",
}

PHOTO_CACHE = {
    folder: {
        "images": [],
        "last_update": None
    }
    for folder in PHOTO_FOLDERS
}

CALENDAR_SERVICE_URL = "http://localhost:5001"
PHOTO_FOLDER_ID = "<key photo folder>"
SERVICE_ACCOUNT_FILE = "service_account.json"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

WEIGHT_FILE = "static/weight/weights.csv"

app = Flask(__name__)


def get_dummy_events():
    # test data, replace later with Google Calendar API / ICS parsing
    return [
        {"time": "9:00 AM", "title": "Team Standup", "calendar": "Work", "color": "#1e88e5"},
        {"time": "12:00 PM", "title": "Lunch with Sam", "calendar": "Personal", "color": "#43a047"},
        {"time": "3:30 PM", "title": "Dentist", "calendar": "Family", "color": "#e53935"},
    ]

@app.route("/")
def dashboard():
    now = datetime.now()
    events = get_dummy_events()
    return render_template(
        "dashboard.html",
        now=now,
        events=events,
    )

@app.route("/api/weather")
def api_weather():
    API_KEY = "<weather api key for openweathermap.org>"

    # Read ?city= parameter
    city = (request.args.get("city") or "fairport").lower()

    # Map cities to coordinates
    CITY_COORDS = {
        "cleveland": {"lat": "41.4993", "lon": "-81.6944"},
        "raleigh":   {"lat": "35.7796", "lon": "-78.6382"},
        "fairport":  {"lat": "43.0982", "lon": "-77.4419"},
    }

    # Default to Fairport if unknown
    coords = CITY_COORDS.get(city, CITY_COORDS["fairport"])

    url = (
        f"https://api.openweathermap.org/data/2.5/weather?"
        f"lat={coords['lat']}&lon={coords['lon']}&appid={API_KEY}&units=imperial"
    )

    try:
        data = requests.get(url).json()
        return jsonify({
            "temp": round(data["main"]["temp"]),
            "condition": data["weather"][0]["main"],
            "icon": data["weather"][0]["icon"],
            "city": city
        })
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/calendar")
def api_calendar():
    try:
        response = requests.get(f"{CALENDAR_SERVICE_URL}/events/today", timeout=2)
        response.raise_for_status()
        return jsonify(response.json())
    except Exception as e:
        print("Error calling calendar service:", e)
        return jsonify({"error": "calendar service unavailable"}), 503






@app.route("/api/calendar/week")
def api_calendar_week():
    try:
        response = requests.get(f"{CALENDAR_SERVICE_URL}/events/week", timeout=2)
        response.raise_for_status()
        return jsonify(response.json())
    except Exception as e:
        print("Error calling calendar service:", e)
        return jsonify({"error": "calendar service unavailable"}), 503





@app.route("/api/weight", methods=["POST"])
def api_save_weight():
    data = request.get_json(silent=True)

    if not data or "weight" not in data:
        print("ERROR: Missing weight or bad JSON:", data)
        return jsonify({"error": "Missing weight"}), 400

    weight = data["weight"]

    # Ensure directory exists
    os.makedirs(os.path.dirname(WEIGHT_FILE), exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")

    try:
        with open(WEIGHT_FILE, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([today, weight])
        print("WROTE:", today, weight)
    except Exception as e:
        print("WRITE ERROR:", e)
        return jsonify({"error": "write failed"}), 500

    return jsonify({"status": "ok", "date": today, "weight": weight})

@app.route("/api/weight/year")
def api_weight_year():
    one_year_ago = datetime.now() - timedelta(days=365)
    results = []

    if not os.path.exists(WEIGHT_FILE):
        return jsonify([])

    with open(WEIGHT_FILE, "r") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) != 2:
                continue

            date_str, weight_str = row
            try:
                entry_date = datetime.strptime(date_str, "%Y-%m-%d")
                if entry_date >= one_year_ago:
                    results.append({
                        "date": date_str,
                        "weight": float(weight_str)
                    })
            except:
                continue

    return jsonify(results)

@app.route("/api/photos")
def api_photos():
    folder = request.args.get("folder", "default")

    if folder not in PHOTO_CACHE:
        return jsonify({"error": "Unknown folder"}), 400

    return jsonify({"images": PHOTO_CACHE[folder]["images"]})

@app.route("/api/photos/local")
def api_photos_local():
    photos_dir = os.path.join(app.static_folder, "photos")

    if not os.path.exists(photos_dir):
        return jsonify({"error": "local photos folder missing"}), 500

    files = []
    for filename in os.listdir(photos_dir):
        if filename.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
            files.append(f"/static/photos/{filename}")

    return jsonify({"images": files})

@app.route("/api/weather/forecast")
def api_weather_forecast():
    API_KEY = "<open weather key>"

    city = (request.args.get("city") or "fairport").lower()

    CITY_COORDS = {
        "cleveland": {"lat": "41.4993", "lon": "-81.6944"},
        "raleigh":   {"lat": "35.7796", "lon": "-78.6382"},
        "fairport":  {"lat": "43.0982", "lon": "-77.4419"},
    }

    coords = CITY_COORDS.get(city, CITY_COORDS["fairport"])

    url = (
        f"https://api.openweathermap.org/data/2.5/forecast?"
        f"lat={coords['lat']}&lon={coords['lon']}&appid={API_KEY}&units=imperial"
    )

    try:
        data = requests.get(url).json()

        if "list" not in data:
            return jsonify({"error": "No forecast data returned"}), 500

        days = {}

        for entry in data["list"]:
            dt = datetime.fromtimestamp(entry["dt"])
            day_key = dt.strftime("%Y-%m-%d")

            temp = entry["main"]["temp"]
            icon = entry["weather"][0]["icon"]
            condition = entry["weather"][0]["main"]

            if day_key not in days:
                days[day_key] = {
                    "temps": [],
                    "icons": [],
                    "conditions": []
                }

            days[day_key]["temps"].append(temp)
            days[day_key]["icons"].append(icon)
            days[day_key]["conditions"].append(condition)

        forecast = []
        for day_key, info in list(days.items())[:5]:  # first 5 days
            date_obj = datetime.strptime(day_key, "%Y-%m-%d")

            forecast.append({
                "date": date_obj.strftime("%a %m/%d"),
                "temp_high": round(max(info["temps"])),
                "temp_low": round(min(info["temps"])),
                "icon": max(set(info["icons"]), key=info["icons"].count),
                "condition": max(set(info["conditions"]), key=info["conditions"].count)
            })

        return jsonify({
            "city": city,
            "forecast": forecast
})



    except Exception as e:
        return jsonify({"error": str(e)})

def refresh_photo_cache():
    global PHOTO_CACHE

    while True:
        for folder_name, folder_id in PHOTO_FOLDERS.items():
            try:
                creds = service_account.Credentials.from_service_account_file(
                    SERVICE_ACCOUNT_FILE, scopes=SCOPES
                )
                service = build("drive", "v3", credentials=creds)

                results = service.files().list(
                    q=f"'{folder_id}' in parents and mimeType contains 'image/'",
                    fields="files(id, name, mimeType)"
                ).execute()

                files = results.get("files", [])

                PHOTO_CACHE[folder_name]["images"] = [
                    f"https://lh3.googleusercontent.com/d/{file['id']}"
                    for file in files
                ]
                PHOTO_CACHE[folder_name]["last_update"] = datetime.now()

                print(f"Refreshed {folder_name}: {len(files)} images")

            except Exception as e:
                print(f"Error refreshing {folder_name}:", e)

        time.sleep(300)  # 5 minutes


if __name__ == "__main__":
    # Access from same machine: http://127.0.0.1:5000
    Thread(target=refresh_photo_cache, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=True)

    
