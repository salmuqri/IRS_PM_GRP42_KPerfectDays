"""Serve Daytrip locally and store demo accounts in frontend/users.json.

Run from the project root:
    python3 frontend/server.py

Then open:
    http://127.0.0.1:8000/frontend/
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

from user_service import UserService


# ============================================================
# CONFIGURATION
# ============================================================

FRONTEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = FRONTEND_DIR.parent

USERS_FILE = FRONTEND_DIR / "users.json"
SHARED_TRIPS_FILE = FRONTEND_DIR / "shared_trips.json"

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
        "interests": trip["interests"],
        "memberCount": len(
            trip["participants"]
        ),
    }


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

    return {
        "candidateIds": trip.get(
            "candidate_ids",
            []
        ),
        "votes": votes.get(
            email,
            {}
        ),
        "likeCounts": like_counts,
    }


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

        if requested_path in {
            "/frontend/users.json",
            "/frontend/users.json.tmp",
            "/frontend/shared_trips.json",
            "/frontend/shared_trips.json.tmp",
        }:
            self.send_error(404)
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

            is_recommendations = (
                suffix.endswith(
                    "/recommendations"
                )
            )

            token = (
                suffix.removesuffix(
                    "/recommendations"
                )
                if is_recommendations
                else suffix
            )

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

            if is_recommendations:

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

                self.send_json(
                    200,
                    {
                        "recommendations":
                        shared_recommendations(
                            trip,
                            email
                        )
                    }
                )

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
            "/frontend/users.json",
            "/frontend/users.json.tmp",
            "/frontend/shared_trips.json",
            "/frontend/shared_trips.json.tmp",
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
            "/api/signup",
            "/api/login",
            "/api/activities",
            "/api/interest",
            "/api/shared-trips",
            "/api/shared-trips/join",
            "/api/shared-trips/vote",
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
            "/api/shared-trips/join",
            "/api/shared-trips/vote",
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
            "/api/shared-trips/join",
            "/api/shared-trips/vote",
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

                interests = (
                    data.get(
                        "interests"
                    )
                )

                candidate_ids = (
                    data.get(
                        "candidateIds"
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
                    or not isinstance(
                        interests,
                        list
                    )
                    or not interests
                    or not isinstance(
                        candidate_ids,
                        list
                    )
                    or not candidate_ids
                ):

                    self.send_json(
                        400,
                        {
                            "error":
                            "The trip needs a destination, dates, and interests."
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

                    "interests": [
                        str(value)[:80]
                        for value
                        in interests[:20]
                    ],

                    "participants": [
                        email
                    ],

                    "candidate_ids": [
                        str(value)[:200]
                        for value
                        in candidate_ids[:1000]
                    ],

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
                            place_id
                            not in trip.get(
                                "candidate_ids",
                                []
                            )
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

                if (
                    original_profile_id
                    and new_profile_id
                    and original_profile_id != new_profile_id
                ):

                    try:

                        friend_result = (
                            user_service.add_friend(
                                original_profile_id=
                                original_profile_id,

                                new_profile_id=
                                new_profile_id
                            )
                        )

                        print(
                            "HAS_FRIEND created:",
                            original_profile_id,
                            "->",
                            new_profile_id
                        )

                    except Exception as error:

                        print(
                            "Neo4j HAS_FRIEND error:",
                            error
                        )

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

                print(
                    "Neo4j user created: "
                    f"{profile_id}"
                )

            except Exception as error:

                print(
                    "Neo4j user creation failed: "
                    f"{error}"
                )

                self.send_json(
                    500,
                    {
                        "error":
                        "Could not create user profile."
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
        "http://127.0.0.1:8000/frontend/"
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