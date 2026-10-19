"""Fetch at least 1,000 tourist places across California using Places API (New).

Set GOOGLE_MAPS_API_KEY in the environment, then run:
    python3 fetch_california_tourist_places.py

The script searches multiple California areas and attraction categories,
retrieves up to three pages (60 results) per query, deduplicates by Place ID,
and stops once the target is reached. Google may return fewer than requested,
so the final count depends on the API results.
"""

import csv
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_URL = "https://places.googleapis.com/v1/places:searchText"
OUTPUT_FILE = "california_tourist_places.csv"
TARGET_PLACES = 1_000
PAGE_SIZE = 20  # Text Search returns at most 20 results per page and 60 per query.
MAX_PAGES_PER_QUERY = 3
MAX_RETRIES = 5
AREAS = [
    "Los Angeles", "San Diego", "San Francisco", "San Jose", "Sacramento",
    "Fresno", "Bakersfield", "Long Beach", "Oakland", "Anaheim", "Santa Barbara",
    "Monterey", "Santa Cruz", "Napa Valley", "Palm Springs", "Yosemite National Park",
    "Lake Tahoe", "Sequoia National Park", "Death Valley National Park", "Joshua Tree National Park",
    "Big Sur", "Mendocino", "San Luis Obispo", "Santa Monica", "Pasadena", "Berkeley",
    "Ventura", "Redding", "Eureka", "Santa Rosa", "Riverside", "San Bernardino",
]
CATEGORIES = [
    ("top tourist attractions", None),
    ("popular restaurants", "restaurant"),
    ("museums and cultural attractions", None),
    ("historic sites and landmarks", None),
    ("parks, gardens, and outdoor attractions", None),
]
QUERIES = [
    (f"{category} in {area}, California", included_type)
    for area in AREAS
    for category, included_type in CATEGORIES
]
FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.types",
        "places.googleMapsUri",
        "nextPageToken",
    ]
)
CSV_COLUMNS = [
    "place_id",
    "name",
    "address",
    "latitude",
    "longitude",
    "types",
    "google_maps_url",
    "search_query",
]


def search_places(api_key, query, page_token=None, included_type=None):
    body_data = {"textQuery": query, "pageSize": PAGE_SIZE}
    if page_token:
        body_data["pageToken"] = page_token
    if included_type:
        body_data["includedType"] = included_type
        body_data["strictTypeFiltering"] = True
    body = json.dumps(body_data).encode("utf-8")
    for attempt in range(MAX_RETRIES + 1):
        request = Request(
            API_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": FIELD_MASK,
            },
        )
        try:
            with urlopen(request, timeout=45) as response:
                result = json.load(response)
                return result.get("places", []), result.get("nextPageToken")
        except HTTPError as error:
            # Retry rate limits and temporary Google server errors only.
            if error.code not in {429, 500, 502, 503, 504} or attempt == MAX_RETRIES:
                raise
            retry_after = error.headers.get("Retry-After")
            delay = int(retry_after) if retry_after and retry_after.isdigit() else min(2**attempt, 30)
            print(f"Temporary HTTP {error.code}; retrying in {delay}s ({attempt + 1}/{MAX_RETRIES})")
        except (URLError, TimeoutError, OSError) as error:
            if attempt == MAX_RETRIES:
                raise
            delay = min(2**attempt, 30)
            print(f"Temporary network error ({error}); retrying in {delay}s ({attempt + 1}/{MAX_RETRIES})")
        time.sleep(delay)


def write_csv(places_by_id):
    temp_file = f"{OUTPUT_FILE}.tmp"
    with open(temp_file, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(places_by_id.values())
    os.replace(temp_file, OUTPUT_FILE)


def main():
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        sys.exit(
            "GOOGLE_MAPS_API_KEY is not set. Set it in your environment, "
            "then run this script again."
        )

    # Resume from the existing CSV so a rerun can continue after a network error.
    places_by_id = {}
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, newline="", encoding="utf-8") as csv_file:
            for row in csv.DictReader(csv_file):
                if row.get("place_id"):
                    places_by_id[row["place_id"]] = row
    print(f"Starting with {len(places_by_id)} existing unique places")

    for query, included_type in QUERIES:
        page_token = None
        for page_number in range(1, MAX_PAGES_PER_QUERY + 1):
            try:
                places, next_page_token = search_places(api_key, query, page_token, included_type)
            except HTTPError as error:
                details = error.read().decode("utf-8", errors="replace")
                sys.exit(f"Google Places API returned HTTP {error.code}: {details}")
            except (URLError, TimeoutError, OSError) as error:
                sys.exit(f"Could not reach Google Places API: {error}")

            for place in places:
                place_id = place.get("id")
                if not place_id:
                    continue
                location = place.get("location", {})
                places_by_id.setdefault(
                    place_id,
                    {
                        "place_id": place_id,
                        "name": place.get("displayName", {}).get("text"),
                        "address": place.get("formattedAddress"),
                        "latitude": location.get("latitude"),
                        "longitude": location.get("longitude"),
                        "types": ";".join(place.get("types", [])),
                        "google_maps_url": place.get("googleMapsUri"),
                        "search_query": query,
                    },
                )

            print(
                f"{len(places_by_id)} unique places after "
                f"{query} (page {page_number}, {len(places)} results)"
            )
            write_csv(places_by_id)
            if len(places_by_id) >= TARGET_PLACES or not next_page_token:
                break
            page_token = next_page_token

        if len(places_by_id) >= TARGET_PLACES:
            break

    print(f"Wrote {len(places_by_id)} unique places to {OUTPUT_FILE}")
    if len(places_by_id) < TARGET_PLACES:
        print(
            f"Only {len(places_by_id)} unique places were returned; "
            "the API results did not reach the 1,000-place target."
        )


if __name__ == "__main__":
    main()
