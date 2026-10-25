"""Serve Daytrip locally and store demo accounts in scripts/users.json.

Run from the project root:
    python3 scripts/server.py

Then open:
    http://127.0.0.1:8000/scripts/
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

from user_service import SIGNUP_INTEREST_MAP, UserService
from planner_jobs import start_job, get_job, claim_shared_publication

# from dotenv import load_dotenv


# ============================================================
# CONFIGURATION
# ============================================================

scripts_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = scripts_DIR.parent

USERS_FILE = scripts_DIR / "users.json"
SHARED_TRIPS_FILE = scripts_DIR / "shared_trips.json"
# load_dotenv(scripts_DIR / ".env")

PBKDF2_ITERATIONS = 310_000
MAX_BODY_BYTES = 32_000

DATA_LOCK = threading.Lock()

SESSIONS = {}


# ============================================================
# NEO4J USER SERVICE
# ============================================================

user_service = UserService()


# ============================================================
# LOCAL USER STORAGE
# ============================================================

def load_users():
    if not USERS_FILE.exists():
        return []

    with USERS_FILE.open(
        "r",
        encoding="utf-8"
    ) as source:
        payload = json.load(source)

    return payload if isinstance(payload, list) else []


def save_users(users):
    temporary_file = USERS_FILE.with_suffix(
        ".json.tmp"
    )

    with temporary_file.open(
        "w",
        encoding="utf-8"
    ) as target:

        json.dump(
            users,
            target,
            ensure_ascii=False,
            indent=2
        )

        target.write("\n")

    try:
        os.chmod(
            temporary_file,
            0o600
        )

    except OSError:
        pass

    os.replace(
        temporary_file,
        USERS_FILE
    )


# ============================================================
# SHARED TRIP STORAGE
# ============================================================

def load_shared_trips():
    if not SHARED_TRIPS_FILE.exists():
        return []

    with SHARED_TRIPS_FILE.open(
        "r",
        encoding="utf-8"
    ) as source:
        payload = json.load(source)

    return payload if isinstance(payload, list) else []


def save_shared_trips(trips):
    temporary_file = SHARED_TRIPS_FILE.with_suffix(
        ".json.tmp"
    )

    with temporary_file.open(
        "w",
        encoding="utf-8"
    ) as target:

        json.dump(
            trips,
            target,
            ensure_ascii=False,
            indent=2
        )

        target.write("\n")

    try:
        os.chmod(
            temporary_file,
            0o600
        )

    except OSError:
        pass

    os.replace(
        temporary_file,
        SHARED_TRIPS_FILE
    )


# ============================================================
# SHARED TRIP HELPERS
# ============================================================

def public_trip(trip):
    return {
        "token": trip["token"],
        "ownerName": trip["owner_name"],
        "city": trip["city"],
        "start": trip["start"],
        "end": trip["end"],
        "memberCount": len(
            trip["participants"]
        ),
    }


def shared_candidate_ids(trip):
    pools = trip.get("candidate_ids_by_member") or {}
    if pools:
        return list(dict.fromkeys(
            place_id
            for member_ids in pools.values()
            for place_id in member_ids
        ))
    # Read old invites until each participant contributes their own preference pool.
    return trip.get("candidate_ids", [])


def shared_recommendations(
    trip,
    email
):
    votes = trip.setdefault(
        "votes",
        {}
    )

    like_counts = {}

    for member_votes in votes.values():

        for place_id, liked in member_votes.items():

            if liked:
                like_counts[place_id] = (
                    like_counts.get(
                        place_id,
                        0
                    )
                    + 1
                )

    pools = trip.get("candidate_ids_by_member") or {}
    member_candidates = pools.get(email)
    if member_candidates is None:
        # Legacy invites used one common list. It remains available until
        # this participant uploads their own signup-interest candidate pool.
        member_candidates = trip.get("candidate_ids", [])

    return {
        "candidateIds": shared_candidate_ids(trip),
        "memberCandidateIds": member_candidates,
        "votes": votes.get(
            email,
            {}
        ),
        "likeCounts": like_counts,
    }


def itinerary_vote_directives(trip):
    votes = trip.get("itinerary_votes") or {}
    threshold = max(1, (len(trip.get("participants", [])) + 1) // 2)
    excluded = {}
    included = {}
    for member_votes in votes.values():
        member_excluded = set()
        member_included = set()
        for place_id, vote in member_votes.items():
            if not isinstance(vote, dict):
                continue
            name = str(vote.get("placeName") or place_id).strip()
            if vote.get("choice") == "replace":
                member_excluded.add(name)
                replacement_id = vote.get("replacementPlaceId")
                replacement_name = str(vote.get("replacementName") or "").strip()
                if replacement_id and replacement_name:
                    member_included.add(replacement_name)
            elif vote.get("choice") == "like":
                member_included.add(name)
        for name in member_excluded:
            excluded[name] = excluded.get(name, 0) + 1
        for name in member_included:
            included[name] = included.get(name, 0) + 1
    exclude_names = sorted(name for name, count in excluded.items() if count >= threshold)
    include_names = sorted(name for name, count in included.items() if count >= threshold)
    directives = []
    if include_names:
        directives.append("Places to include: " + ", ".join(include_names) + ".")
    if exclude_names:
        directives.append("Places not to include: " + ", ".join(exclude_names) + ".")
    return directives


def public_itinerary_votes(trip, email):
    all_votes = trip.get("itinerary_votes") or {}
    counts = {}
    for member_votes in all_votes.values():
        for place_id, vote in member_votes.items():
            if not isinstance(vote, dict):
                continue
            entry = counts.setdefault(place_id, {"likes": 0, "replacements": 0})
            entry["likes" if vote.get("choice") == "like" else "replacements"] += 1
    return {"mine": all_votes.get(email, {}), "counts": counts}


def group_member_context(trip, users):
    members = []
    for email in trip.get("participants", []):
        account = next((user for user in users if user.get("email") == email), None)
        if not account:
            continue
        members.append({
            "interests": account.get("interests", []) if isinstance(account.get("interests"), list) else [],
        })
    return members


def save_shared_itinerary(token, result, base_prompt=""):
    with DATA_LOCK:
        trips = load_shared_trips()
        trip = next((item for item in trips if item.get("token") == token), None)
        if not trip:
            return
        previous = trip.get("latest_itinerary") or {}
        trip["latest_itinerary"] = {
            "version": int(previous.get("version", 0)) + 1,
            "basePrompt": base_prompt,
            "plan": result.get("plan", []),
            "slots": result.get("slots", []),
            "itinerary": result.get("itinerary", ""),
        }
        save_shared_trips(trips)


# ============================================================
# PASSWORD HASHING
# ============================================================

def password_hash(
    password,
    salt
):
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        PBKDF2_ITERATIONS
    ).hex()


# ============================================================
# HTTP HANDLER
# ============================================================

class DaytripHandler(
    SimpleHTTPRequestHandler
):

    def __init__(
        self,
        *args,
        **kwargs
    ):
        super().__init__(
            *args,
            directory=str(PROJECT_ROOT),
            **kwargs
        )


    # ========================================================
    # LOGGING
    # ========================================================

    def log_message(
        self,
        format_string,
        *args
    ):
        super().log_message(
            format_string,
            *args
        )


    # ========================================================
    # AUTHENTICATION
    # ========================================================

    def authenticated_email(self):

        authorization = self.headers.get(
            "Authorization",
            ""
        )

        scheme, _, token = (
            authorization.partition(" ")
        )

        if (
            scheme.lower() != "bearer"
            or not token
        ):
            return None

        with DATA_LOCK:
            return SESSIONS.get(token)


    # ========================================================
    # JSON RESPONSE
    # ========================================================

    def send_json(
        self,
        status,
        payload
    ):

        data = json.dumps(
            payload
        ).encode("utf-8")

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(data))
        )

        self.send_header(
            "Cache-Control",
            "no-store"
        )

        self.end_headers()

        self.wfile.write(data)


    # ========================================================
    # READ JSON REQUEST
    # ========================================================

    def read_json_body(self):

        try:
            length = int(
                self.headers.get(
                    "Content-Length",
                    "0"
                )
            )

        except ValueError:
            return None

        if (
            length <= 0
            or length > MAX_BODY_BYTES
        ):
            return None

        try:
            return json.loads(
                self.rfile.read(length)
            )

        except (
            json.JSONDecodeError,
            UnicodeDecodeError
        ):
            return None


    # ========================================================
    # GET
    # ========================================================

    def do_GET(self):

        requested_path = unquote(
            urlparse(
                self.path
            ).path
        )

        # ----------------------------------------------------
        # Prevent direct access to local JSON files
        # ----------------------------------------------------

        if requested_path.startswith(('/scripts/planner_results/', '/scripts/__pycache__/', '/scripts/.env')) or requested_path in ('/users.json','/test.py','/.env','/scripts/.env','/scripts/.env.example'):
            self.send_error(404)
            return
        if requested_path in {
            "/scripts/users.json",
            "/scripts/users.json.tmp",
            "/scripts/shared_trips.json",
            "/scripts/shared_trips.json.tmp",
        }:
            self.send_error(404)
            return

        if requested_path.startswith('/api/itineraries/'):
            email = self.authenticated_email()
            if not email:
                self.send_json(401, {'error': 'Please log in.'})
                return
            job = get_job(requested_path.split('/')[-1], email)
            if not job:
                self.send_json(404, {'error': 'Unknown itinerary job.'})
                return
            publication = claim_shared_publication(job['job_id'], email) if job.get('status') == 'completed' else None
            if publication:
                save_shared_itinerary(publication['shared_trip_id'], job,
                                       publication.get('shared_base_prompt', ''))
            self.send_json(200, job)
            return

        # ----------------------------------------------------
        # SESSION
        # ----------------------------------------------------

        if requested_path == "/api/session":

            email = self.authenticated_email()

            if not email:
                self.send_json(
                    401,
                    {
                        "error":
                        "Please log in again."
                    }
                )
                return

            try:
                user = next(
                    (
                        item
                        for item in load_users()
                        if item.get("email") == email
                    ),
                    None
                )

            except (
                OSError,
                json.JSONDecodeError
            ):
                user = None

            if not user:
                self.send_json(
                    401,
                    {
                        "error":
                        "Please log in again."
                    }
                )
                return

            self.send_json(
                200,
                {
                    "user": {
                        "profileId":
                        user.get("profile_id"),
                        "name":
                        user["name"],
                        "email":
                        user["email"],
                        "interests":
                        user.get("interests", []),
                    }
                }
            )

            return

        # ----------------------------------------------------
        # SHARED TRIP GET
        # ----------------------------------------------------

        if requested_path.startswith(
            "/api/shared-trips/"
        ):

            suffix = (
                requested_path
                .removeprefix(
                    "/api/shared-trips/"
                )
            )

            suffix_type = next((name for name in ("recommendations", "state", "itinerary")
                                if suffix.endswith("/" + name)), "")
            token = suffix.removesuffix("/" + suffix_type) if suffix_type else suffix

            if not re.fullmatch(
                r"[A-Za-z0-9_-]{20,80}",
                token
            ):
                self.send_json(
                    404,
                    {
                        "error":
                        "This trip invite link is invalid."
                    }
                )
                return

            try:

                with DATA_LOCK:

                    trip = next(
                        (
                            item
                            for item
                            in load_shared_trips()
                            if item.get(
                                "token"
                            ) == token
                        ),
                        None
                    )

            except (
                OSError,
                json.JSONDecodeError
            ):
                self.send_json(
                    500,
                    {
                        "error":
                        "Shared trip data could not be read."
                    }
                )
                return

            if not trip:
                self.send_json(
                    404,
                    {
                        "error":
                        "This trip invite link is no longer available."
                    }
                )
                return

            if suffix_type:

                email = (
                    self.authenticated_email()
                )

                if (
                    not email
                    or email
                    not in trip.get(
                        "participants",
                        []
                    )
                ):
                    self.send_json(
                        403,
                        {
                            "error":
                            "Join this trip before viewing its recommendations."
                        }
                    )
                    return

                if suffix_type == "recommendations":
                    payload = {"recommendations": shared_recommendations(trip, email)}
                elif suffix_type == "state":
                    users = load_users()
                    payload = {
                        "trip": public_trip(trip),
                        "members": group_member_context(trip, users),
                        "votes": public_itinerary_votes(trip, email),
                        "voteDirectives": itinerary_vote_directives(trip),
                        "latestItinerary": trip.get("latest_itinerary"),
                    }
                else:
                    payload = {
                        "trip": public_trip(trip),
                        "votes": public_itinerary_votes(trip, email),
                        "latestItinerary": trip.get("latest_itinerary"),
                    }
                self.send_json(200, payload)

                return

            self.send_json(
                200,
                {
                    "trip":
                    public_trip(trip)
                }
            )

            return

        super().do_GET()


    # ========================================================
    # HEAD
    # ========================================================

    def do_HEAD(self):

        requested_path = unquote(
            urlparse(
                self.path
            ).path
        )

        if requested_path in {
            "/scripts/users.json",
            "/scripts/users.json.tmp",
            "/scripts/shared_trips.json",
            "/scripts/shared_trips.json.tmp",
        }:
            self.send_error(404)
            return

        super().do_HEAD()


    # ========================================================
    # POST
    # ========================================================

    def do_POST(self):

        endpoint = urlparse(
            self.path
        ).path

        # ----------------------------------------------------
        # ALLOWED ENDPOINTS
        # ----------------------------------------------------

        if endpoint not in {
            "/api/itineraries",
            "/api/signup",
            "/api/login",
            "/api/activities",
            "/api/interest",
            "/api/shared-trips",
            "/api/shared-trips/candidates",
            "/api/shared-trips/join",
            "/api/shared-trips/vote",
            "/api/shared-trips/itinerary-vote",
        }:

            self.send_json(
                404,
                {"error": "Not found."}
            )

            return

        # ----------------------------------------------------
        # READ BODY
        # ----------------------------------------------------

        data = self.read_json_body()

        if not isinstance(
            data,
            dict
        ):

            self.send_json(
                400,
                {
                    "error":
                    "Please submit valid account details."
                }
            )

            return


        if endpoint == '/api/itineraries':
            email = self.authenticated_email()
            if not email:
                self.send_json(401, {'error':'Please log in.'})
                return
            users = load_users()
            account = next((x for x in users if x.get('email') == email), None)
            if not account or not account.get('profile_id'):
                self.send_json(400, {'error':'Account is missing a KG profile.'})
                return
            prompt = str(data.get('prompt') or '').strip()
            if not 20 <= len(prompt) <= 6000:
                self.send_json(400, {'error':'Describe your trip in 20–6000 characters.'})
                return
            ids = [account['profile_id']]
            participant_accounts = [account]
            token = str(data.get('sharedTripId') or '').strip()
            shared = None
            if token:
                shared = next((t for t in load_shared_trips() if t.get('token') == token
                              and email in t.get('participants', [])), None)
                if not shared:
                    self.send_json(403, {'error':'You are not in this shared trip.'})
                    return
                members = set(shared.get('participants', []))
                for user in users:
                    if user.get('email') in members and user.get('profile_id'):
                        ids.append(user['profile_id'])
                        participant_accounts.append(user)
                if data.get('refreshGroup') is True:
                    for directive in itinerary_vote_directives(shared):
                        listed_names = [name.strip().rstrip(".") for name in directive.split(":", 1)[-1].split(",")]
                        if not all(name and name.casefold() in prompt.casefold() for name in listed_names):
                            prompt += "\n\n" + directive
            ids = list(dict.fromkeys(ids))
            profile_interests = {}
            try:
                seen_profiles = set()
                for traveler in participant_accounts:
                    profile_id = traveler['profile_id']
                    if profile_id in seen_profiles:
                        continue
                    seen_profiles.add(profile_id)
                    selected = traveler.get('interests', [])
                    if not isinstance(selected, list):
                        selected = []
                    selected = [value for value in selected if isinstance(value, str)]
                    user_service.save_signup_preferences(profile_id, selected)
                    user_service.map_persona(profile_id)
                    profile_interests[profile_id] = selected
            except Exception as error:
                print(f"Neo4j signup preference sync failed: {error}")
                self.send_json(500, {'error':'Could not sync traveler preferences to Neo4j.'})
                return
            job_id = start_job(prompt, ids, email, profile_interests, shared_trip_id=token or None,
                               shared_base_prompt=str(data.get('basePrompt') or '').strip())
            self.send_json(202, {'job_id':job_id, 'status':'running', 'profiles':ids})
            return

        # ====================================================
        # USER PREFERENCES
        # ====================================================

        if endpoint in {
            "/api/activities",
            "/api/interest",
           
        }:

            # ------------------------------------------------
            # AUTHENTICATE USER
            # ------------------------------------------------

            email = (
                self.authenticated_email()
            )

            if not email:

                self.send_json(
                    401,
                    {
                        "error":
                        "Please log in again."
                    }
                )

                return

            # ------------------------------------------------
            # LOAD USER
            # ------------------------------------------------

            try:

                with DATA_LOCK:
                    users = load_users()

            except (
                OSError,
                json.JSONDecodeError
            ):

                self.send_json(
                    500,
                    {
                        "error":
                        "The local account file could not be read."
                    }
                )

                return

            user = next(
                (
                    item
                    for item in users
                    if item.get(
                        "email"
                    ) == email
                ),
                None
            )

            if not user:

                self.send_json(
                    401,
                    {
                        "error":
                        "User account could not be found."
                    }
                )

                return

            profile_id = (
                user.get(
                    "profile_id"
                )
            )

            if not profile_id:

                self.send_json(
                    500,
                    {
                        "error":
                        "User does not have a profile ID."
                    }
                )

                return


            # ================================================
            # ACTIVITIES
            # One-time onboarding
            # ================================================

            if endpoint == "/api/activities":

                activities = data.get(
                    "activities",
                    []
                )

                if (
                    not isinstance(
                        activities,
                        list
                    )
                    or not activities
                ):

                    self.send_json(
                        400,
                        {
                            "error":
                            "Select at least one activity."
                        }
                    )

                    return

                try:

                    # Save activities
                    user_service.add_activities(
                        profile_id,
                        activities
                    )

                    # Initial persona mapping
                    user_service.map_persona(
                        profile_id
                    )

                except Exception as error:

                    print(
                        "Neo4j activity/persona "
                        f"error: {error}"
                    )

                    self.send_json(
                        500,
                        {
                            "error":
                            "Could not save activities."
                        }
                    )

                    return

                self.send_json(
                    200,
                    {
                        "success": True,
                        "activities":
                        activities
                    }
                )

                return


            # ================================================
            #  LIKE/DISLIKE
            # ================================================

            if endpoint == "/api/interest":

                place_id = str(
                    data.get("placeId", "")
                ).strip()

                feedback = str(
                    data.get("feedback", "")
                ).strip().lower()

                if not place_id:
                    self.send_json(
                        400,
                        {"error": "Place ID is required."}
                    )
                    return

                if feedback not in {"like", "dislike"}:
                    self.send_json(
                        400,
                        {"error": "Feedback must be like or dislike."}
                    )
                    return

                try:
                    updates = user_service.update_preference_from_place(
                        profile_id=profile_id,
                        place_id=place_id,
                        feedback=feedback
                    )

                    if not updates:
                        self.send_json(
                            404,
                            {
                                "error":
                                "Could not map this place to an activity."
                            }
                        )
                        return

                    persona = user_service.map_persona(
                        profile_id
                    )

                except Exception as error:
                    print(
                        f"Neo4j preference error: {error}"
                    )

                    self.send_json(
                        500,
                        {
                            "error":
                            "Could not update preference."
                        }
                    )
                    return

                self.send_json(
                    200,
                    {
                        "success": True,
                        "feedback": feedback,
                        "placeId": place_id,
                        "updates": updates,
                        "persona": persona
                    }
                )
                return


        # ====================================================
        # SIGNUP / LOGIN / SHARED TRIPS
        # ====================================================

        email = str(
            data.get(
                "email",
                ""
            )
        ).strip().lower()

        password = str(
            data.get(
                "password",
                ""
            )
        )


        # ====================================================
        # SHARED TRIP AUTHENTICATION
        # ====================================================

        if endpoint in {
            "/api/shared-trips",
            "/api/shared-trips/candidates",
            "/api/shared-trips/join",
            "/api/shared-trips/vote",
            "/api/shared-trips/itinerary-vote",
        }:

            email = (
                self.authenticated_email()
            )

            if not email:

                self.send_json(
                    401,
                    {
                        "error":
                        "Please log in before sharing or joining a trip."
                    }
                )

                return


        # ====================================================
        # VALIDATE EMAIL
        # ====================================================

        if not re.fullmatch(
            r"[^\s@]+@[^\s@]+\.[^\s@]+",
            email
        ):

            self.send_json(
                400,
                {
                    "error":
                    "Enter a valid email address."
                }
            )

            return


        # ====================================================
        # LOAD USERS
        # ====================================================

        try:

            with DATA_LOCK:
                users = load_users()

        except (
            OSError,
            json.JSONDecodeError
        ):

            self.send_json(
                500,
                {
                    "error":
                    "The local account file could not be read."
                }
            )

            return


        # ====================================================
        # SHARED TRIPS
        # ====================================================

        if endpoint in {
            "/api/shared-trips",
            "/api/shared-trips/candidates",
            "/api/shared-trips/join",
            "/api/shared-trips/vote",
            "/api/shared-trips/itinerary-vote",
        }:

            if not any(
                account.get(
                    "email"
                ) == email
                for account in users
            ):

                self.send_json(
                    401,
                    {
                        "error":
                        "Log in before sharing or joining a trip."
                    }
                )

                return


            # ================================================
            # CREATE SHARED TRIP
            # ================================================

            if endpoint == "/api/shared-trips":

                city = str(
                    data.get(
                        "city",
                        ""
                    )
                ).strip()

                start = str(
                    data.get(
                        "start",
                        ""
                    )
                )

                end = str(
                    data.get(
                        "end",
                        ""
                    )
                )

                owner = next(
                    account
                    for account in users
                    if account.get(
                        "email"
                    ) == email
                )

                if (
                    not city
                    or not start
                    or not end
                ):

                    self.send_json(
                        400,
                        {
                            "error":
                            "The trip needs a destination and dates."
                        }
                    )

                    return

                trip = {

                    "token":
                    secrets.token_urlsafe(
                        24
                    ),

                    "owner_email":
                    email,

                    "owner_profile_id":
                    owner.get(
                        "profile_id"
                    ),

                    "owner_name":
                    owner.get(
                        "name",
                        "A Daytrip traveler"
                    ),

                    "city":
                    city,

                    "start":
                    start,

                    "end":
                    end,

                    "participants": [
                        email
                    ],

                    "candidate_ids_by_member": {},

                    "votes": {},
                }

                try:

                    with DATA_LOCK:

                        trips = (
                            load_shared_trips()
                        )

                        trips.append(
                            trip
                        )

                        save_shared_trips(
                            trips
                        )

                except (
                    OSError,
                    json.JSONDecodeError
                ):

                    self.send_json(
                        500,
                        {
                            "error":
                            "Could not save the shared trip."
                        }
                    )

                    return

                self.send_json(
                    201,
                    {
                        "trip":
                        public_trip(
                            trip
                        )
                    }
                )

                return


            # ================================================
            # ADD A MEMBER'S PERSONALIZED CANDIDATE POOL
            # ================================================

            if endpoint == "/api/shared-trips/candidates":
                token = str(data.get("token") or "").strip()
                raw_ids = data.get("candidateIds", [])
                if not isinstance(raw_ids, list):
                    self.send_json(400, {"error": "Candidate IDs must be a list."})
                    return
                candidate_ids = list(dict.fromkeys(
                    str(value)[:200]
                    for value in raw_ids
                    if isinstance(value, str) and value.strip()
                ))[:1000]
                try:
                    with DATA_LOCK:
                        trips = load_shared_trips()
                        trip = next((item for item in trips if item.get("token") == token), None)
                        if not trip or email not in trip.get("participants", []):
                            self.send_json(404, {"error": "This shared trip was not found."})
                            return
                        pools = trip.setdefault("candidate_ids_by_member", {})
                        legacy = trip.pop("candidate_ids", [])
                        if legacy and trip.get("owner_email"):
                            pools.setdefault(trip["owner_email"], legacy)
                        trip.pop("interests", None)
                        pools[email] = candidate_ids
                        save_shared_trips(trips)
                        recommendations = shared_recommendations(trip, email)
                except (OSError, json.JSONDecodeError):
                    self.send_json(500, {"error": "Could not save personalized group recommendations."})
                    return
                self.send_json(200, {"recommendations": recommendations})
                return


            if endpoint == "/api/shared-trips/itinerary-vote":
                token = str(data.get("token") or "").strip()
                place_id = str(data.get("placeId") or "").strip()[:200]
                place_name = str(data.get("placeName") or place_id).strip()[:300]
                choice = data.get("choice")
                if not token or not place_id or choice not in {"like", "replace"}:
                    self.send_json(400, {"error": "Choose like or replace for an itinerary place."})
                    return
                vote = {"choice": choice, "placeName": place_name}
                if choice == "replace":
                    replacement_id = str(data.get("replacementPlaceId") or "").strip()[:200]
                    replacement_name = str(data.get("replacementName") or "").strip()[:300]
                    if not replacement_id or not replacement_name:
                        self.send_json(400, {"error": "Choose an available replacement place."})
                        return
                    vote.update(replacementPlaceId=replacement_id, replacementName=replacement_name)
                try:
                    with DATA_LOCK:
                        trips = load_shared_trips()
                        trip = next((item for item in trips if item.get("token") == token), None)
                        if not trip or email not in trip.get("participants", []):
                            self.send_json(404, {"error": "This shared trip was not found."})
                            return
                        trip.setdefault("itinerary_votes", {}).setdefault(email, {})[place_id] = vote
                        save_shared_trips(trips)
                        directives = itinerary_vote_directives(trip)
                except (OSError, json.JSONDecodeError):
                    self.send_json(500, {"error": "Could not save your itinerary vote."})
                    return
                self.send_json(200, {"voteDirectives": directives})
                return

            # ================================================
            # SHARED TRIP VOTE
            # ================================================

            if endpoint == "/api/shared-trips/vote":

                token = str(
                    data.get(
                        "token",
                        ""
                    )
                )

                place_id = str(
                    data.get(
                        "placeId",
                        ""
                    )
                )

                liked = data.get(
                    "liked"
                )

                try:

                    with DATA_LOCK:

                        trips = (
                            load_shared_trips()
                        )

                        trip = next(
                            (
                                item
                                for item
                                in trips
                                if item.get(
                                    "token"
                                ) == token
                            ),
                            None
                        )

                        if (
                            not trip
                            or email
                            not in trip.get(
                                "participants",
                                []
                            )
                        ):

                            self.send_json(
                                404,
                                {
                                    "error":
                                    "This shared trip was not found."
                                }
                            )

                            return

                        if (
                            place_id not in shared_candidate_ids(trip)
                            or not isinstance(
                                liked,
                                bool
                            )
                        ):

                            self.send_json(
                                400,
                                {
                                    "error":
                                    "That place cannot be rated for this trip."
                                }
                            )

                            return

                        trip.setdefault(
                            "votes",
                            {}
                        ).setdefault(
                            email,
                            {}
                        )[place_id] = liked

                        save_shared_trips(
                            trips
                        )

                        recommendation_data = (
                            shared_recommendations(
                                trip,
                                email
                            )
                        )

                except (
                    OSError,
                    json.JSONDecodeError
                ):

                    self.send_json(
                        500,
                        {
                            "error":
                            "Could not save your group rating."
                        }
                    )

                    return

                self.send_json(
                    200,
                    {
                        "recommendations":
                        recommendation_data
                    }
                )

                return

            # ================================================
            # JOIN SHARED TRIP
            # ================================================

            if endpoint == "/api/shared-trips/join":

                token = str(
                    data.get(
                        "token",
                        ""
                    )
                ).strip()

                try:

                    with DATA_LOCK:

                        trips = (
                            load_shared_trips()
                        )

                        trip = next(
                            (
                                item
                                for item
                                in trips
                                if item.get(
                                    "token"
                                ) == token
                            ),
                            None
                        )

                        # ----------------------------------------
                        # Add joining user to participants
                        # ----------------------------------------

                        if (
                            trip
                            and email
                            not in trip.get(
                                "participants",
                                []
                            )
                        ):

                            trip.setdefault(
                                "participants",
                                []
                            ).append(
                                email
                            )

                            save_shared_trips(
                                trips
                            )

                except (
                    OSError,
                    json.JSONDecodeError
                ):

                    self.send_json(
                        500,
                        {
                            "error":
                            "Could not update the shared trip."
                        }
                    )

                    return

                # ================================================
                # CHECK THAT TRIP EXISTS
                # ================================================

                if not trip:

                    self.send_json(
                        404,
                        {
                            "error":
                            "This trip invite link is no longer available."
                        }
                    )

                    return

                # ================================================
                # CREATE HAS_FRIEND RELATIONSHIP
                # ================================================

                original_profile_id = trip.get(
                    "owner_profile_id"
                )

                joining_user = next(
                    (
                        account
                        for account
                        in users
                        if account.get(
                            "email"
                        ) == email
                    ),
                    None
                )

                new_profile_id = (
                    joining_user.get(
                        "profile_id"
                    )
                    if joining_user
                    else None
                )

                if not original_profile_id or not new_profile_id:
                    self.send_json(
                        500,
                        {"error": "The inviter or invitee has no Neo4j profile."}
                    )
                    return

                if original_profile_id != new_profile_id:
                    try:
                        friend_result = user_service.add_friend(
                            original_profile_id=original_profile_id,
                            new_profile_id=new_profile_id,
                        )
                        if not friend_result:
                            raise RuntimeError("Neo4j did not create the inviter relationship.")
                    except Exception as error:
                        print(f"Neo4j inviter relationship failed: {error}")
                        self.send_json(
                            500,
                            {"error": "Could not link your Neo4j profile to the inviter."}
                        )
                        return

                # ================================================
                # RETURN SHARED TRIP
                # ================================================

                self.send_json(
                    200,
                    {
                        "trip":
                        public_trip(
                            trip
                        )
                    }
                )

                return


        # ====================================================
        # FIND EXISTING USER
        # ====================================================

        existing = next(
            (
                user
                for user in users
                if user.get(
                    "email"
                ) == email
            ),
            None
        )


        # ====================================================
        # SIGNUP
        # ====================================================

        if endpoint == "/api/signup":

            name = str(
                data.get(
                    "name",
                    ""
                )
            ).strip()

            interests = data.get("interests")
            if not isinstance(interests, list) or not interests:
                self.send_json(400, {"error": "Choose at least one interest."})
                return
            interests = list(dict.fromkeys(
                str(value).strip()[:80]
                for value in interests
                if isinstance(value, str) and value.strip()
            ))
            if not interests:
                self.send_json(400, {"error": "Choose at least one valid interest."})
                return
            unsupported = [value for value in interests if value not in SIGNUP_INTEREST_MAP]
            if unsupported:
                self.send_json(400, {"error": "One or more selected interests are not supported."})
                return

            if (
                len(name) < 1
                or len(name) > 120
            ):

                self.send_json(
                    400,
                    {
                        "error":
                        "Enter your name (up to 120 characters)."
                    }
                )

                return

            if len(password) < 8:

                self.send_json(
                    400,
                    {
                        "error":
                        "Use a password with at least 8 characters."
                    }
                )

                return

            if existing:

                self.send_json(
                    409,
                    {
                        "error":
                        "An account with that email already exists. Try logging in."
                    }
                )

                return


            # ================================================
            # CREATE USER
            # ================================================

            salt = (
                secrets
                .token_bytes(16)
                .hex()
            )

            profile_id = (
                f"P"
                f"{secrets.randbelow(1_000_000):06d}"
            )

            user = {

                "profile_id":
                profile_id,

                "name":
                name,

                "email":
                email,

                "interests":
                interests,

                "salt":
                salt,

                "password_hash":
                password_hash(
                    password,
                    salt
                ),
            }


            # ================================================
            # SAVE USER LOCALLY
            # ================================================

            users.append(
                user
            )

            try:

                save_users(
                    users
                )

            except OSError:

                self.send_json(
                    500,
                    {
                        "error":
                        "Could not save the local account file."
                    }
                )

                return


            # ================================================
            # CREATE USER IN NEO4J
            # ================================================

            try:

                user_service.merge_user(

                    profile_id=
                    profile_id,

                    name=
                    name,

                    email=
                    email
                )

                user_service.save_signup_preferences(
                    profile_id,
                    interests
                )

                persona = user_service.map_persona(
                    profile_id
                )
                if not persona:
                    raise RuntimeError("No Neo4j persona matched the user's signup interests.")

                print(
                    "Neo4j user created: "
                    f"{profile_id}"
                )

            except Exception as error:

                users = [saved for saved in users if saved.get("email") != email]
                try:
                    save_users(users)
                except OSError:
                    pass

                print(
                    "Neo4j user/preference creation failed: "
                    f"{error}"
                )

                self.send_json(
                    500,
                    {
                        "error":
                        "Could not save account preferences to Neo4j."
                    }
                )

                return


        # ====================================================
        # LOGIN
        # ====================================================

        else:

            if not existing:

                self.send_json(
                    401,
                    {
                        "error":
                        "Email or password is incorrect."
                    }
                )

                return

            candidate = password_hash(

                password,

                existing.get(
                    "salt",
                    ""
                )
            )

            if not hmac.compare_digest(

                candidate,

                existing.get(
                    "password_hash",
                    ""
                )
            ):

                self.send_json(
                    401,
                    {
                        "error":
                        "Email or password is incorrect."
                    }
                )

                return

            user = existing


        # ====================================================
        # CREATE SESSION
        # AFTER SUCCESSFUL SIGNUP / LOGIN
        # ====================================================

        session_token = (
            secrets.token_urlsafe(
                32
            )
        )

        with DATA_LOCK:

            SESSIONS[
                session_token
            ] = user["email"]


        # ====================================================
        # AUTH RESPONSE
        # ====================================================

        self.send_json(
            200,
            {
                "user": {

                    "profileId":
                    user.get(
                        "profile_id"
                    ),

                    "name":
                    user["name"],

                    "email":
                    user["email"],

                    "interests":
                    user.get("interests", []),
                },

                "sessionToken":
                session_token,
            }
        )


# ============================================================
# MAIN
# ============================================================

def main():

    server = ThreadingHTTPServer(
        (
            "127.0.0.1",
            8000
        ),
        DaytripHandler
    )

    print(
        "Daytrip is running at "
        "http://127.0.0.1:8000/scripts/"
    )

    print(
        "Local account data will be saved to "
        f"{USERS_FILE}"
    )

    try:
        server.serve_forever()

    except KeyboardInterrupt:
        print(
            "\nStopping Daytrip server."
        )

    finally:
        server.server_close()


if __name__ == "__main__":
    main()