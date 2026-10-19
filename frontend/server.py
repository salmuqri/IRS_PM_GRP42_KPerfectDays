"""Serve Daytrip locally and store demo accounts in frontend/users.json.

Run from the project root:
    python3 frontend/server.py

Then open http://127.0.0.1:8000/frontend/
The server listens only on this computer. Passwords are stored as salted
PBKDF2 hashes; they are never written to the JSON file in plain text.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


FRONTEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = FRONTEND_DIR.parent
USERS_FILE = FRONTEND_DIR / "users.json"
SHARED_TRIPS_FILE = FRONTEND_DIR / "shared_trips.json"
PBKDF2_ITERATIONS = 310_000
MAX_BODY_BYTES = 32_000
DATA_LOCK = threading.Lock()
SESSIONS = {}


def load_users():
    if not USERS_FILE.exists():
        return []
    with USERS_FILE.open("r", encoding="utf-8") as source:
        payload = json.load(source)
    return payload if isinstance(payload, list) else []


def save_users(users):
    temporary_file = USERS_FILE.with_suffix(".json.tmp")
    with temporary_file.open("w", encoding="utf-8") as target:
        json.dump(users, target, ensure_ascii=False, indent=2)
        target.write("\n")
    try:
        os.chmod(temporary_file, 0o600)
    except OSError:
        pass
    os.replace(temporary_file, USERS_FILE)


def load_shared_trips():
    if not SHARED_TRIPS_FILE.exists():
        return []
    with SHARED_TRIPS_FILE.open("r", encoding="utf-8") as source:
        payload = json.load(source)
    return payload if isinstance(payload, list) else []


def save_shared_trips(trips):
    temporary_file = SHARED_TRIPS_FILE.with_suffix(".json.tmp")
    with temporary_file.open("w", encoding="utf-8") as target:
        json.dump(trips, target, ensure_ascii=False, indent=2)
        target.write("\n")
    try:
        os.chmod(temporary_file, 0o600)
    except OSError:
        pass
    os.replace(temporary_file, SHARED_TRIPS_FILE)


def public_trip(trip):
    return {
        "token": trip["token"],
        "ownerName": trip["owner_name"],
        "city": trip["city"],
        "start": trip["start"],
        "end": trip["end"],
        "interests": trip["interests"],
        "memberCount": len(trip["participants"]),
    }


def shared_recommendations(trip, email):
    votes = trip.setdefault("votes", {})
    like_counts = {}
    for member_votes in votes.values():
        for place_id, liked in member_votes.items():
            if liked:
                like_counts[place_id] = like_counts.get(place_id, 0) + 1
    return {
        "candidateIds": trip.get("candidate_ids", []),
        "votes": votes.get(email, {}),
        "likeCounts": like_counts,
    }


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    ).hex()


class DaytripHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PROJECT_ROOT), **kwargs)

    def log_message(self, format_string, *args):
        # Keep request logs compact and avoid printing request bodies.
        super().log_message(format_string, *args)

    def do_GET(self):
        requested_path = unquote(urlparse(self.path).path)
        if requested_path in {
            "/frontend/users.json", "/frontend/users.json.tmp",
            "/frontend/shared_trips.json", "/frontend/shared_trips.json.tmp",
        }:
            self.send_error(404)
            return
        if requested_path == "/api/session":
            email = self.authenticated_email()
            if not email:
                self.send_json(401, {"error": "Please log in again."})
                return
            try:
                user = next((item for item in load_users() if item.get("email") == email), None)
            except (OSError, json.JSONDecodeError):
                user = None
            if not user:
                self.send_json(401, {"error": "Please log in again."})
                return
            self.send_json(200, {"user": {"name": user["name"], "email": user["email"]}})
            return
        if requested_path.startswith("/api/shared-trips/"):
            suffix = requested_path.removeprefix("/api/shared-trips/")
            is_recommendations = suffix.endswith("/recommendations")
            token = suffix.removesuffix("/recommendations") if is_recommendations else suffix
            if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", token):
                self.send_json(404, {"error": "This trip invite link is invalid."})
                return
            try:
                with DATA_LOCK:
                    trip = next((item for item in load_shared_trips() if item.get("token") == token), None)
            except (OSError, json.JSONDecodeError):
                self.send_json(500, {"error": "Shared trip data could not be read."})
                return
            if not trip:
                self.send_json(404, {"error": "This trip invite link is no longer available."})
                return
            if is_recommendations:
                email = self.authenticated_email()
                if not email or email not in trip.get("participants", []):
                    self.send_json(403, {"error": "Join this trip before viewing its recommendations."})
                    return
                self.send_json(200, {"recommendations": shared_recommendations(trip, email)})
                return
            self.send_json(200, {"trip": public_trip(trip)})
            return
        super().do_GET()

    def authenticated_email(self):
        authorization = self.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        with DATA_LOCK:
            return SESSIONS.get(token)

    def do_HEAD(self):
        requested_path = unquote(urlparse(self.path).path)
        if requested_path in {
            "/frontend/users.json", "/frontend/users.json.tmp",
            "/frontend/shared_trips.json", "/frontend/shared_trips.json.tmp",
        }:
            self.send_error(404)
            return
        super().do_HEAD()

    def send_json(self, status, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY_BYTES:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def do_POST(self):
        endpoint = urlparse(self.path).path
        if endpoint not in {
            "/api/signup", "/api/login", "/api/shared-trips", "/api/shared-trips/join",
            "/api/shared-trips/vote",
        }:
            self.send_json(404, {"error": "Not found."})
            return
        data = self.read_json_body()
        if not isinstance(data, dict):
            self.send_json(400, {"error": "Please submit valid account details."})
            return

        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))
        if endpoint in {"/api/shared-trips", "/api/shared-trips/join", "/api/shared-trips/vote"}:
            email = self.authenticated_email()
            if not email:
                self.send_json(401, {"error": "Please log in before sharing or joining a trip."})
                return
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            self.send_json(400, {"error": "Enter a valid email address."})
            return

        try:
            with DATA_LOCK:
                users = load_users()
        except (OSError, json.JSONDecodeError):
            self.send_json(500, {"error": "The local account file could not be read."})
            return

        if endpoint in {"/api/shared-trips", "/api/shared-trips/join", "/api/shared-trips/vote"}:
            if not any(account.get("email") == email for account in users):
                self.send_json(401, {"error": "Log in before sharing or joining a trip."})
                return
            if endpoint == "/api/shared-trips":
                city = str(data.get("city", "")).strip()
                start = str(data.get("start", ""))
                end = str(data.get("end", ""))
                interests = data.get("interests")
                candidate_ids = data.get("candidateIds")
                owner = next(account for account in users if account.get("email") == email)
                if (not city or not start or not end or not isinstance(interests, list) or not interests
                        or not isinstance(candidate_ids, list) or not candidate_ids):
                    self.send_json(400, {"error": "The trip needs a destination, dates, and interests."})
                    return
                trip = {
                    "token": secrets.token_urlsafe(24),
                    "owner_email": email,
                    "owner_name": owner.get("name", "A Daytrip traveler"),
                    "city": city,
                    "start": start,
                    "end": end,
                    "interests": [str(value)[:80] for value in interests[:20]],
                    "participants": [email],
                    "candidate_ids": [str(value)[:200] for value in candidate_ids[:1000]],
                    "votes": {},
                }
                try:
                    with DATA_LOCK:
                        trips = load_shared_trips()
                        trips.append(trip)
                        save_shared_trips(trips)
                except (OSError, json.JSONDecodeError):
                    self.send_json(500, {"error": "Could not save the shared trip."})
                    return
                self.send_json(201, {"trip": public_trip(trip)})
                return

            if endpoint == "/api/shared-trips/vote":
                token = str(data.get("token", ""))
                place_id = str(data.get("placeId", ""))
                liked = data.get("liked")
                try:
                    with DATA_LOCK:
                        trips = load_shared_trips()
                        trip = next((item for item in trips if item.get("token") == token), None)
                        if not trip or email not in trip.get("participants", []):
                            self.send_json(404, {"error": "This shared trip was not found."})
                            return
                        if place_id not in trip.get("candidate_ids", []) or not isinstance(liked, bool):
                            self.send_json(400, {"error": "That place cannot be rated for this trip."})
                            return
                        trip.setdefault("votes", {}).setdefault(email, {})[place_id] = liked
                        save_shared_trips(trips)
                        recommendation_data = shared_recommendations(trip, email)
                except (OSError, json.JSONDecodeError):
                    self.send_json(500, {"error": "Could not save your group rating."})
                    return
                self.send_json(200, {"recommendations": recommendation_data})
                return

            token = str(data.get("token", ""))
            try:
                with DATA_LOCK:
                    trips = load_shared_trips()
                    trip = next((item for item in trips if item.get("token") == token), None)
                    if trip and email not in trip["participants"]:
                        trip["participants"].append(email)
                        save_shared_trips(trips)
            except (OSError, json.JSONDecodeError):
                self.send_json(500, {"error": "Could not update the shared trip."})
                return
            if not trip:
                self.send_json(404, {"error": "This trip invite link is no longer available."})
                return
            self.send_json(200, {"trip": public_trip(trip)})
            return

        existing = next((user for user in users if user.get("email") == email), None)
        if endpoint == "/api/signup":
            name = str(data.get("name", "")).strip()
            if len(name) < 1 or len(name) > 120:
                self.send_json(400, {"error": "Enter your name (up to 120 characters)."})
                return
            if len(password) < 8:
                self.send_json(400, {"error": "Use a password with at least 8 characters."})
                return
            if existing:
                self.send_json(409, {"error": "An account with that email already exists. Try logging in."})
                return
            salt = secrets.token_bytes(16).hex()
            user = {
                "name": name,
                "email": email,
                "salt": salt,
                "password_hash": password_hash(password, salt),
            }
            users.append(user)
            try:
                save_users(users)
            except OSError:
                self.send_json(500, {"error": "Could not save the local account file."})
                return
        else:
            if not existing:
                self.send_json(401, {"error": "Email or password is incorrect."})
                return
            candidate = password_hash(password, existing.get("salt", ""))
            if not hmac.compare_digest(candidate, existing.get("password_hash", "")):
                self.send_json(401, {"error": "Email or password is incorrect."})
                return
            user = existing

        session_token = secrets.token_urlsafe(32)
        with DATA_LOCK:
            SESSIONS[session_token] = user["email"]
        self.send_json(200, {
            "user": {"name": user["name"], "email": user["email"]},
            "sessionToken": session_token,
        })


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 8000), DaytripHandler)
    print("Daytrip is running at http://127.0.0.1:8000/frontend/")
    print(f"Local account data will be saved to {USERS_FILE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Daytrip server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
