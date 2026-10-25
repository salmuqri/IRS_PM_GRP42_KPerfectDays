"""K Perfect Days itinerary planner (extracted from research notebook v7.5.2)."""
import ast
import itertools
import json
import math
import os
import re
import statistics
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from getpass import getpass
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from neo4j import GraphDatabase
from openai import OpenAI
from ortools.sat.python import cp_model
from pydantic import BaseModel, Field
from IPython.display import Markdown, display

# One configuration block. All service URLs and integration parameters are here.
SELF_ID = os.environ['KPD_OWNER_PROFILE_ID']
IS_SHARED_TRIP = os.environ.get('KPD_IS_SHARED_TRIP', 'false').lower() == 'true'
TIMEZONE = 'America/Los_Angeles'
TODAY = datetime.now(ZoneInfo(TIMEZONE)).date()
PROMPT_MODEL = 'gpt-6-luna'
ITINERARY_MODEL = 'gpt-6.1-sol'
NEO4J_URI = os.environ['NEO4J_URI']
NEO4J_USER = os.environ.get('NEO4J_USERNAME', 'neo4j')
GOOGLE_DETAILS_URL = 'https://places.googleapis.com/v1/places/'
ORS_MATRIX_URL = 'https://api.heigit.org/openrouteservice/v2/matrix/'
FORECAST_URL = 'https://api.open-meteo.com/v1/forecast'
ARCHIVE_URL = 'https://archive-api.open-meteo.com/v1/archive'
GEOCODE_URL = 'https://geocoding-api.open-meteo.com/v1/search'
import uuid
RUN_ID = datetime.now(ZoneInfo(TIMEZONE)).strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
OUTPUT_DIR = Path(os.environ.get('KPD_OUTPUT_ROOT', 'kpd_runs')) / RUN_ID
OUTPUT_DIR.mkdir(parents=True,exist_ok=True)
print('Run ID:',RUN_ID)

# Write the request exactly as a traveler would say it. The parser interprets
# "tomorrow", "next week", "this Friday", etc. relative to TODAY in California.
USER_PROMPT = os.environ['KPD_TRIP_PROMPT']
# Replace USER_PROMPT above with your 21-night multi-city test after this smoke test passes.

MAX_NIGHTS = 30
USE_GOOGLE = False                  # Fully optional; never needed to run a plan.
MAX_GOOGLE_LOOKUPS_PER_CITY = 0
USE_OSM_HOURS = False               # Public Overpass commonly times out; OFF for stable tests.
OVERPASS_URL = 'https://overpass-api.de/api/interpreter'
OVERPASS_MAX_PER_CITY = 8           # Only applies if enabled; queried in tiny batches.
OVERPASS_BATCH_SIZE = 3
OVERPASS_MATCH_RADIUS_M = 70
CANDIDATES_PER_CITY = 34            # Actual target, provided enough KG places exist.
TOUR_TOP_CANDIDATES = CANDIDATES_PER_CITY  # Candidate count feeds feasible-tour beam search.
TOUR_BEAM_WIDTH = 75               # Number of partial day tours retained at each depth.
DAY_OPTION_LIMIT = 140             # Exact, feasible options offered to CP-SAT per day.
MAX_STOPS_FULL_DAY = 4
MAX_STOPS_TRANSFER_DAY = 3
CANDIDATES_MIN_QUALITY = 0           # change to 3 to tighten quality gate
SOLVER_SECONDS = 60
REQUIRE_ORS = True                   # never silently estimate a road route
DIAGNOSTIC_PRINTS = True
# Set a real base here, e.g. {'San Francisco': (37.7801,-122.4193)};
# otherwise the city centre is a LABELED placeholder, never claimed to be a hotel.
HOTELS = {}
# Geoapify Place Details: lat/lon lookup because your KG stores GOOGLE place IDs, not Geoapify IDs.
GEOAPIFY_DETAILS_URL = 'https://api.geoapify.com/v2/place-details'
GEOAPIFY_REQUEST_LIMIT_PER_CITY = 34      # ~34 credits/city with a free key; no Google billing needed
GEOAPIFY_MAX_DISTANCE_M = 120
GEOAPIFY_MIN_NAME_MATCH = 0.78
# Scores are model-derived and are NOT user satisfaction measurements.
W = dict(group=2, fairness=2, request=95, quality=0, travel=1, variety=9, iconic=3, daily=5)
# PRIORITY: avoid empty sightseeing days.  These are planning-quality safeguards,
# not daily *fairness*: individual persona satisfaction remains trip-level.
# The optimizer may accept an underfilled day if routing, venue hours, hard wishes,
# non-repetition, or the KG leave no better feasible full-trip solution.
MEAL_ONLY_FULL_DAY_PENALTY = 35000
ONE_PRIMARY_FULL_DAY_PENALTY = 12500
# Less expensive to select two proper daytime experiences than to concentrate
# all culture/nature on a few days and serve only lunch on the others.

# Higher group weight prioritizes shared TRIP enjoyment; fairness is a TRIP-level safeguard.
# v7.4 counts diversity across the trip instead of rewarding repeated daily categories.


# Geolocated, self-guided PUBLIC experiences. These are not alleged Neo4j facts or booked tours.
# Coordinates are approximate public starting points; ORS must independently route each.
# GPT-6 can nominate only a catalog ID: it does not invent coordinates, times or events.
SELF_GUIDED_CATALOG = [
 # ID, overnight city, name, latitude, longitude, experience types, minutes, indicative area
 ('SF_OCEAN_BEACH','San Francisco','Ocean Beach waterfront time',37.7594,-122.5107,'beach;coastline;walking',135,'Ocean Beach, San Francisco'),
 ('SF_CRISSY_FIELD','San Francisco','Crissy Field waterfront walk',37.8035,-122.4671,'beach;coastline;walking;photography',105,'Crissy Field, San Francisco'),
 ('SF_LANDS_END','San Francisco','Lands End coastal trail walk',37.7811,-122.5033,'hiking_area;coastline;walking',150,'Lands End Trail, San Francisco'),
 ('SF_NORTH_BEACH','San Francisco','North Beach neighborhood exploration',37.7993,-122.4095,'neighborhood;sightseeing;walking',100,'North Beach, San Francisco'),
 ('SF_CHINATOWN','San Francisco','Chinatown self-guided neighborhood walk',37.7941,-122.4078,'neighborhood;sightseeing;walking',100,'Chinatown, San Francisco'),
 ('SF_EMBARCADERO','San Francisco','Embarcadero waterfront promenade',37.7967,-122.3950,'coastline;walking;sightseeing',90,'The Embarcadero, San Francisco'),
 ('LA_SANTA_MONICA','Los Angeles','Santa Monica Beach time',34.0106,-118.4960,'beach;coastline;walking',150,'Santa Monica State Beach, Santa Monica'),
 ('LA_VENICE','Los Angeles','Venice Beach and boardwalk walk',33.9850,-118.4695,'beach;coastline;walking',130,'Venice Beach Boardwalk, Los Angeles'),
 ('LA_ABBOT_KINNEY','Los Angeles','Abbot Kinney neighborhood browsing',33.9902,-118.4666,'neighborhood;sightseeing;shopping',95,'Abbot Kinney Boulevard, Venice, Los Angeles'),
 ('LA_ARTS_DISTRICT','Los Angeles','Downtown Arts District walk',34.0415,-118.2331,'neighborhood;sightseeing;art',100,'Arts District, Los Angeles'),
 ('LA_GRIFFITH_HIKE','Los Angeles','Griffith Park scenic walk',34.1190,-118.3003,'hiking_area;park;photography',155,'Griffith Observatory area, Griffith Park'),
 ('LA_LITTLE_TOKYO','Los Angeles','Little Tokyo neighborhood walk',34.0494,-118.2400,'neighborhood;sightseeing;shopping',90,'Little Tokyo, Los Angeles'),
 ('SD_LA_JOLLA','San Diego','La Jolla Shores beach time',32.8556,-117.2570,'beach;coastline;walking',145,'La Jolla Shores, San Diego'),
 ('SD_CORONADO','San Diego','Coronado Beach walk',32.6848,-117.1868,'beach;coastline;walking',125,'Coronado Central Beach, Coronado'),
 ('SD_GASLAMP','San Diego','Gaslamp Quarter neighborhood exploration',32.7109,-117.1604,'neighborhood;sightseeing;walking',90,'Gaslamp Quarter, San Diego'),
 ('SD_BALBOA','San Diego','Balboa Park gardens and pathways',32.7312,-117.1468,'park;walking;sightseeing',130,'Balboa Park, San Diego'),
 ('SD_TORREY_PINES','San Diego','Torrey Pines coastal hike',32.9212,-117.2525,'hiking_area;coastline;walking',155,'Torrey Pines State Natural Reserve, San Diego'),
 ('NAPA_RIVERFRONT','Napa','Downtown Napa riverfront walk',38.2985,-122.2828,'neighborhood;walking;sightseeing',100,'Napa Riverfront, Napa'),
 ('NAPA_OXBOW','Napa','Oxbow neighborhood and market browsing',38.3014,-122.2810,'neighborhood;shopping;walking',90,'Oxbow District, Napa'),
 ('YOSEMITE_MEADOW','Yosemite Valley','Cook’s Meadow scenic loop',37.7460,-119.5900,'hiking_area;park;photography',100,'Cook’s Meadow, Yosemite Valley'),
 ('YOSEMITE_MIRROR','Yosemite Valley','Mirror Lake trail outing',37.7479,-119.5487,'hiking_area;nature;photography',170,'Mirror Lake Trail, Yosemite Valley'),
 ('MONTEREY_COAST','Monterey','Coastal Recreation Trail walk',36.6133,-121.8980,'coastline;walking',100,'Monterey Bay Coastal Recreation Trail'),
 ('SB_WATERFRONT','Santa Barbara','East Beach waterfront walk',34.4168,-119.6796,'beach;coastline;walking',115,'East Beach, Santa Barbara'),
 ('SC_WEST_CLIFF','Santa Cruz','West Cliff Drive coastal walk',36.9493,-122.0429,'coastline;walking',125,'West Cliff Drive, Santa Cruz')
]
SELF_GUIDED_INDEX={row[0]:row for row in SELF_GUIDED_CATALOG}


# Keep integration details in the notebook, but never publish live credentials.
# Colab Secrets (key icon): NEO4J_PASSWORD, OPENAI_API_KEY, ORS_API_KEY, GEOAPIFY_API_KEY
from getpass import getpass

def secret(key, label, *, required=True):
    val = os.environ.get(key, '').strip()
    if not val:
        try:
            from google.colab import userdata
            val = str(userdata.get(key) or '').strip()
        except Exception:
            pass
    if not val:
        raise RuntimeError(f'Missing {key}; set it in your environment')
    if required and not val:
        raise ValueError(f'Missing {key}. Set it in Colab Secrets or enter it at the prompt.')
    return val

#NEO4J_PASSWORD = secret('NEO4J_PASSWORD', 'Neo4j Aura password')
#OPENAI_KEY = secret('OPENAI_API_KEY', 'OpenAI API key')
#ORS_KEY = secret('ORS_API_KEY', 'OpenRouteService / HeiGIT key')
#GEOAPIFY_KEY = secret('GEOAPIFY_API_KEY', 'Geoapify API key', required=False)
NEO4J_PASSWORD = secret('NEO4J_PASSWORD', 'Neo4j password')
OPENAI_KEY = secret('OPENAI_API_KEY', 'OpenAI API key')
ORS_KEY = secret('ORS_API_KEY', 'OpenRouteService API key')
GEOAPIFY_KEY = os.environ.get('GEOAPIFY_API_KEY', '').strip()

client = OpenAI(api_key=OPENAI_KEY, timeout=120)
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
driver.verify_connectivity()
http = requests.Session()
http.headers.update({'User-Agent':'KPerfectDays-NUS-ISS-research/0.73 (academic proof of concept)'})

def cypher(statement, **params):
    with driver.session() as session:
        return [r.data() for r in session.run(statement, **params)]

def get_json(url, *, params=None, headers=None, method='get', body=None, attempts=2):
    for attempt in range(attempts):
        try:
            response = http.request(method, url, params=params, headers=headers,
                                    json=body, timeout=35)
            if response.status_code in (401,403,429):
                raise RuntimeError(f'HTTP {response.status_code} from {url.split("?")[0]}')
            if response.status_code in (500,502,503,504) and attempt+1<attempts:
                time.sleep(attempt+1); continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            if attempt+1>=attempts: raise
            time.sleep(attempt+1)

print('Connected to Neo4j | Geoapify:', 'enabled' if GEOAPIFY_KEY else 'not set')

class Destination(BaseModel):
    city: str
    nights: int = Field(ge=1)

class Wish(BaseModel):
    theme: str
    city: str | None = None
    minimum: int = Field(ge=0, default=0)
    desired_count: int = Field(default=1, ge=1, le=7)
    mandatory: bool = False
    named_place: str | None = None

class TripRequest(BaseModel):
    start_date: str | None = None
    destinations: list[Destination]
    wishes: list[Wish]
    pace: str = 'balanced'
    night_owl_recovery: bool = True
    city_start_hour: int = Field(default=10, ge=6, le=13)
    nature_start_hour: int = Field(default=8, ge=5, le=12)
    weekend_nightlife: bool = False
    dietary: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    prohibited_themes: list[str] = Field(default_factory=list)
    prohibited_places: list[str] = Field(default_factory=list)
    suggested_self_guided_ids: list[str] = Field(default_factory=list)

CATALOG_LABELS = {item[0]:f'{item[2]} (near {item[1]})' for item in SELF_GUIDED_CATALOG}

EXTRACT_INSTRUCTIONS = '''You extract trip requirements, not itinerary suggestions.
Scope: California destinations only. Distinguish nights (hotel stays) and days.
Interpret ALL relative dates using the supplied California local reference date.
Use that date and timezone for 'today', 'tomorrow', 'next Monday', 'this weekend',
'next month', and so on; for 'next month' without a day choose the 1st of next month
as a transparent default and mention that assumption in notes. For 'next week'\nwithout a weekday choose the following Monday; for 'next weekend' choose the next Saturday.
Always output start_date as YYYY-MM-DD (ISO format).
If genuinely impossible to infer a start date, use null. Keep destination order.
Return wishes ONLY actually present in THIS USER MESSAGE. Do not remember prior
prompts or introduce Universal, theme parks, nightclubs, etc. on your own.
Allowed themes: sightseeing, architecture, history, museum, art, beach,
nature, hiking, theme_park, food, fine_dining, live_music, nightlife,
nightclub, literature, shopping, photography, neighborhoods, wildlife.
Mark mandatory only for genuine instructions such as 'must', 'definitely',
'cannot miss', or named places explicitly requested as fixed visits.
'We like museums' is a preference; 'we must visit SFMOMA' is mandatory.
For geographic scope, an LA hotel stay includes sensible Orange County/Anaheim DAY TRIPS.
Do not infer that visiting Los Angeles means visiting Disneyland. Disneyland is an OPTION
only if the user requests a theme park or explicitly names it; Universal may be better.
Never convert a wish into a named attraction. Do not confuse occasional dining
with a demand for multiple destination restaurants daily.
If a wish is soft, minimum=0; desired_count expresses a preferred number of visits,
not a hard constraint. For 'a few beach days' use desired_count=3, for
'a couple of museums' use 2. If mandatory, minimum is a realistic count.
Never turn 'go out on weekends' into minimum=10; it is a preference unless explicitly 'must'.
IMPORTANT: detect NEGATIVE instructions. 'No theme parks', 'skip museums',
'avoid nightclubs', 'do not include Disneyland' become prohibited_themes or
prohibited_places (a hard ban), NOT a positive wish. Preserve exclusions even
if other group members like the excluded category. Empty lists if none.
Suggest 2–6 relevant, varied SELF-GUIDED experience IDs PER CITY from the
provided catalog when they suit this prompt and its known destinations.
Use your cultural and geographic knowledge to choose between them, but output
ONLY exact IDs from the provided catalog; never fabricate IDs, geography, hours
or a specific event. Include a beach if the user asks for a beach day, and a
neighborhood walk if requested. Suggested IDs are options, not booked stops.
'We want a good time' or 'do it all' does NOT mean a hard requirement.
Set city_start_hour/nature_start_hour from the request where explicitly specified.
Preserve dietary needs, sleep-in-after-nightlife, budget/pace and unusual rules in notes.
Do not claim a weather forecast exists for the entire trip.'''

# Provide the current local reference date to the parser, never to the traveler.
EXTRACT_INSTRUCTIONS += (f'\nCALIFORNIA CURRENT DATE: {TODAY:%A, %B %d, %Y}. '
                         f'Time zone: {TIMEZONE}. Interpret relative dates using this anchor. '
                         'Verified candidate IDs you may suggest: '+json.dumps(CATALOG_LABELS,ensure_ascii=False))

response = client.responses.parse(
    model=PROMPT_MODEL,
    input=[{'role':'system','content':EXTRACT_INSTRUCTIONS},
           {'role':'user','content':USER_PROMPT}],
    text_format=TripRequest,
)
if response.output_parsed is None:
    raise RuntimeError('Trip parsing failed. Check the OpenAI response.')
trip = response.output_parsed.model_dump()
if not trip['start_date']:
    raise ValueError('Could not resolve a travel date from the message. Add a phrase such as next Friday or March 12.')

# Deterministic failsafe for prohibitions if the LLM misses a clear negative phrase.
# Do not misread numeric caps ('no more than two parks') as a total ban.
NEGATIVE_CATEGORIES = [
    (r'\b(?:no|without|avoid|skip|don[\x27’]?t\s+want(?:\s+any)?|do\s+not\s+include)\s+(?:any\s+|all\s+)?(?:theme|amusement)\s+parks?\b','theme_park'),
    (r'\b(?:no|without|avoid|skip|don[\x27’]?t\s+want(?:\s+any)?)\s+(?:any\s+)?nightclubs?\b','nightclub'),
    (r'\b(?:no|without|avoid|skip|don[\x27’]?t\s+want(?:\s+any)?)\s+(?:any\s+)?museums?\b','museum'),
    (r'\b(?:no|without|avoid|skip|don[\x27’]?t\s+want(?:\s+any)?)\s+(?:any\s+)?hikes?\b','hiking'),
]
for pattern,theme in NEGATIVE_CATEGORIES:
    if re.search(pattern,USER_PROMPT,re.I):
        trip['prohibited_themes'].append(theme)
# Common named-attraction prohibitions are also guarded deterministically.
for forbidden_venue in ('disneyland','universal studios','seaworld','belmont park'):
    pattern=(r"\b(?:no|avoid|skip|without|don[\x27’]?t\s+want|do\s+not\s+include)"
             r"\s+(?:visits?\s+to\s+)?"+re.escape(forbidden_venue)+r"\b")
    if re.search(pattern,USER_PROMPT,re.I):
        trip['prohibited_places'].append(forbidden_venue)

trip['prohibited_themes']=list(dict.fromkeys(
    re.sub(r'[^a-z0-9]+','_',str(t).lower()).strip('_') for t in trip['prohibited_themes'] if t))
trip['prohibited_places']=list(dict.fromkeys(str(t).lower().strip() for t in
    trip['prohibited_places'] if str(t).strip()))
# A prohibition always wins over a contradictory positive theme/wish.
trip['wishes']=[w for w in trip['wishes'] if
    re.sub(r'[^a-z0-9]+','_',w['theme'].lower()).strip('_') not in trip['prohibited_themes']
    and not (w.get('named_place') and any(t in w['named_place'].lower() for t in trip['prohibited_places']))]
trip['suggested_self_guided_ids']=list(dict.fromkeys(
    s for s in trip['suggested_self_guided_ids'] if s in SELF_GUIDED_INDEX))
print('Hard exclusions:',trip['prohibited_themes'],trip['prohibited_places'])
print('LLM-suggested geographic experiences:',trip['suggested_self_guided_ids'])

start_date = date.fromisoformat(trip['start_date'])
if start_date < TODAY:
    raise ValueError('Trip start date is in the past.')
if not trip['destinations'] or sum(d['nights'] for d in trip['destinations']) > MAX_NIGHTS:
    raise ValueError(f'Trip needs 1–{MAX_NIGHTS} nights total.')

profile_ids = json.loads(os.environ['KPD_PROFILE_IDS'])
profile_ids = list(dict.fromkeys(profile_ids))
PROFILE_INTERESTS = json.loads(os.environ.get('KPD_PROFILE_INTERESTS', '{}'))

days = []
cursor = start_date
for index, dest in enumerate(trip['destinations']):
    for j in range(dest['nights']):
        days.append({'date':cursor.isoformat(), 'city':dest['city'],
                     'transfer':index > 0 and j == 0, 'trip_day':len(days)+1})
        cursor += timedelta(days=1)
print('Profiles:', profile_ids)
print('Destinations:', [(d['city'],d['nights']) for d in trip['destinations']])
print('Explicit wishes:', trip['wishes'])


def fetch_users(ids):
    data = {pid:{'activities':{}, 'interests':{}, 'persona_ids':[]} for pid in ids}
    rows = cypher('''
      MATCH (u:User) WHERE u.profileId IN $ids
      OPTIONAL MATCH (u)-[:SIMILAR_TO]->(p:Persona)
      OPTIONAL MATCH (p)-[edge:HAS_ACTIVITY]->(a:P_Activity)
      RETURN u.profileId AS id, p.personaId AS persona_id,
             a.activityType AS activity, edge.weight AS weight
    ''', ids=ids)
    known = {r['id'] for r in rows}
    if missing := set(ids)-known:
        raise ValueError(f'Unknown KG user IDs: {sorted(missing)}')
    for row in rows:
        item=data[row['id']]
        if row['persona_id'] is not None and row['persona_id'] not in item['persona_ids']:
            item['persona_ids'].append(row['persona_id'])
        if row['activity']:
            key=str(row['activity']).lower().strip()
            item['activities'][key]=max(item['activities'].get(key,0),
                                        float(row['weight']) if row['weight'] is not None else 0.5)
    # Direct interest fallback is available already; it is NOT claimed to be collaborative filtering.
    for row in cypher('''
      MATCH (u:User)-[r:HAS_INTEREST]->(i:Interest)
      WHERE u.profileId IN $ids
      RETURN u.profileId AS id, i.interest AS concept,
             r.preferenceStrength AS strength, r.confidence AS confidence
    ''', ids=ids):
        if row['concept']:
            s=str(row['strength'] or '').lower()
            value={'high':1.0,'moderate':0.65,'medium':0.65,'low':0.20}.get(s,0.45)
            data[row['id']]['interests'][str(row['concept']).lower()]=value * float(row['confidence'] or 0.5)
    return data

users=fetch_users(profile_ids)
if DIAGNOSTIC_PRINTS:
    for uid, row in users.items():
        print(uid, 'persona',row['persona_ids'],'activities',
              sorted(row['activities'].items(),key=lambda x:-x[1])[:5])

raw_places=cypher('''
  MATCH (p:Place_)-[:HAS_CATEGORY]->(c:Category)
  OPTIONAL MATCH (c)-[r:ACTIVITY_SIMILARITY]->(a:P_Activity)
  WITH p, collect(DISTINCT c.category) AS categories,
       collect(DISTINCT {name:a.activityType, weight:r.weight}) AS matches
  RETURN p.placeId AS id, p.name AS name, p.address AS address,
         p.latitude AS lat, p.longitude AS lon, p.types AS types,
         p.googleMapsUrl AS map_url, categories, matches
''')
if not raw_places:raise RuntimeError('New California graph has no linked Place_ nodes.')
if len(raw_places)!=len({p['id'] for p in raw_places}):
    raise RuntimeError('Duplicate placeId in Neo4j; fix before recommending.')
print('Neo4j candidates:',len(raw_places))

def tokens(value):
    if value is None:return set()
    if isinstance(value,(list,tuple,set)):
        return set().union(*(tokens(v) for v in value)) if value else set()
    s=str(value).strip()
    if s.startswith('[') and s.endswith(']'):
        try:
            obj=ast.literal_eval(s)
            if isinstance(obj,(list,tuple,set)):return tokens(obj)
        except (ValueError,SyntaxError):pass
    return {re.sub(r'[^a-z0-9]+','_',v.lower()).strip('_')
            for v in re.split(r'[;,|\n]+',s) if v.strip() and v.strip().lower()!='canvas'}

# Detailed, interpretable place taxonomy. Category text alone is not proof of type.
TYPE_TAGS={
    'museum':{'museum'}, 'art_museum':{'museum','art'}, 'art_gallery':{'art'},
    'historical_landmark':{'history','sightseeing'}, 'historical_place':{'history','sightseeing'},
    'monument':{'history','sightseeing'}, 'tourist_attraction':{'sightseeing'},
    'observation_deck':{'sightseeing','architecture'}, 'aquarium':{'wildlife','museum'},
    'zoo':{'wildlife','nature'}, 'national_park':{'nature','hiking'},
    'state_park':{'nature','hiking'}, 'park':{'nature'},'beach':{'beach','nature'},
    'hiking_area':{'hiking','nature'}, 'botanical_garden':{'nature'},
    'amusement_park':{'theme_park'},'theme_park':{'theme_park'},
    'night_club':{'nightclub','nightlife'}, 'nightclub':{'nightclub','nightlife'},
    'bar':{'nightlife'},'pub':{'nightlife'},
    'live_music_venue':{'live_music','nightlife'},'concert_hall':{'live_music','art'},
    'performing_arts_theater':{'art'},'movie_theater':{'art'},
    'restaurant':{'food'},'fine_dining_restaurant':{'food','fine_dining'},
    'cafe':{'food'}, 'coffee_shop':{'food'},'bakery':{'food'},
    'book_store':{'literature','shopping'},'library':{'literature'},
    'shopping_mall':{'shopping'},'shopping_center':{'shopping'},'clothing_store':{'shopping'},
    'neighborhood':{'neighborhoods','sightseeing'},'pedestrian_zone':{'neighborhoods','sightseeing'},
    'coastline':{'nature','sightseeing'},'beach_resort':{'beach'},
    'art_gallary_museum':{'art','museum'}, # known KG spelling
    'sightseeing':{'sightseeing'},'walking':{'sightseeing'},
}
GROUPS={
    'theme_park':'major', 'museum':'culture','art':'culture','history':'culture',
    'architecture':'culture','literature':'culture','sightseeing':'sights',
    'nature':'outdoors','beach':'outdoors','hiking':'outdoors','wildlife':'outdoors',
    'food':'meal','fine_dining':'meal','nightclub':'evening','nightlife':'evening',
    'live_music':'evening','shopping':'shopping','photography':'sights',
    'neighborhoods':'sights'
}
WISH_ALIASES={'art_museum':'art','art_gallery':'art','modern_art':'art',
              'iconic':'sightseeing','landmarks':'sightseeing',
              'restaurants':'food','dining':'food','outdoor':'nature',
              'theme_parks':'theme_park','clubs':'nightclub','walking_tour':'neighborhoods',
              'neighborhood':'neighborhoods'}

# These are priority BOOSTS only when corresponding POI exists in the actual KG.
# No attractions are fabricated, and none is mandatory unless THIS trip requests it.
CALIFORNIA_ANCHORS={
  'San Francisco':['Golden Gate Bridge','Alcatraz','San Francisco Museum of Modern Art',
                   'SFMOMA','Palace of Fine Arts','Ferry Building','Lands End',
                   'Twin Peaks','Exploratorium','Chinatown','Golden Gate Park'],
  'Los Angeles':['Griffith Observatory','Getty Center','Getty Museum',
                 'Santa Monica Pier','Hollywood Sign','The Broad','Venice Beach',
                 'Los Angeles County Museum of Art','Grand Central Market',
                 'Huntington Library'],
  'San Diego':['Balboa Park','San Diego Zoo','La Jolla Cove',
               'USS Midway Museum','Coronado Beach','Torrey Pines',
               'Old Town San Diego','Cabrillo National Monument'],
  'Anaheim':[],  # Anaheim must be explicitly eligible as a stay or LA day trip
  'Monterey':['Monterey Bay Aquarium','Cannery Row','Point Lobos'],
  'Santa Barbara':['Santa Barbara County Courthouse','Old Mission Santa Barbara'],
  'Sacramento':['California State Capitol','California State Railroad Museum'],
  'Yosemite Valley':['Yosemite Falls','Tunnel View','Glacier Point','El Capitan'],
}
CITY_COORDS={
  'San Francisco':(37.7749,-122.4194),'Los Angeles':(34.0522,-118.2437),
  'San Diego':(32.7157,-117.1611),'Anaheim':(33.8366,-117.9143),
  'Monterey':(36.6002,-121.8947),'Santa Barbara':(34.4208,-119.6982),
  'Sacramento':(38.5816,-121.4944),'Yosemite Valley':(37.7461,-119.5959),
  'Napa':(38.2975,-122.2869),'Palm Springs':(33.8303,-116.5453),
  'Santa Cruz':(36.9741,-122.0308),'Long Beach':(33.7701,-118.1937),
  'Oakland':(37.8044,-122.2712),'San Jose':(37.3382,-121.8863)
}

def canonical(theme):
    t=re.sub(r'[^a-z0-9]+','_',str(theme).lower()).strip('_')
    return WISH_ALIASES.get(t,t)


def haversine(a,b,c,d):
    p1,p2=math.radians(float(a)),math.radians(float(c))
    dp=p2-p1;dl=math.radians(float(d)-float(b))
    z=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 6371*2*math.atan2(math.sqrt(z),math.sqrt(max(0,1-z)))


def clean_name_city(s):
    name=str(s or '').lower().strip()
    return {'la':'los angeles','l.a.':'los angeles','anaheim california':'anaheim'}.get(name,name)


def wish_city_eligible(p,wish,city):
    requested=clean_name_city(wish.get('city'))
    destination=clean_name_city(city)
    if not requested or requested==destination:return True
    if destination=='los angeles' and requested=='anaheim':
        return haversine(p['lat'],p['lon'],*CITY_COORDS['Anaheim'])<=23
    return False


def coordinates(city):
    if city in CITY_COORDS:return CITY_COORDS[city]
    payload=get_json(GEOCODE_URL,params={'name':city,'count':10,'language':'en','format':'json'})
    results=[r for r in payload.get('results',[]) if r.get('admin1')=='California'
             and r.get('country_code')=='US']
    if not results:raise ValueError(f'{city}: not resolved to California. Use a specific city.')
    return (results[0]['latitude'],results[0]['longitude'])

centers={d['city']:coordinates(d['city']) for d in days}


def category_tags(rawtypes, cats, googletypes=None, primary=None):
    kg_types=tokens(rawtypes)
    gtypes=tokens(googletypes)
    all_types=kg_types | gtypes
    tags=set().union(*(TYPE_TAGS.get(v,set()) for v in all_types)) if all_types else set()
    weak=tokens(cats)
    for k in weak:
        if k in ('art_gallary_museum','museum','art_gallery'):tags|={'art'}
        if k in ('sightseeing','photography'):tags.add('sightseeing')
        if k=='hiking':tags.add('nature')
    if primary in ('park','city_park','national_park','state_park') or (
        ('park' in kg_types or 'park' in gtypes) and 'amusement_park' not in gtypes
        and 'theme_park' not in gtypes
    ):
        tags.discard('theme_park')
    return tags


def classify(p):
    tags=category_tags(p['types'],p['categories'],
                       (p.get('google') or {}).get('types'),
                       (p.get('google') or {}).get('primaryType'))
    low=p['name'].lower()
    if 'museum store' in low or 'gift shop' in low:
        tags.discard('museum');tags.discard('art');tags.add('shopping')
    if 'water park' in low:tags.discard('theme_park')
    if 'theme_park' in tags and 'nightclub' in tags:tags.discard('nightclub')
    if 'park' in tokens(p['types']) and not any(x in low for x in
          ('universal studios','disneyland','california adventure','seaworld','six flags','legoland')):
        tags.discard('theme_park')
    if 'nightclub' in tags and 'night_club' not in tokens((p.get('google') or {}).get('types')) \
       and 'night_club' not in tokens(p['types']) and 'nightclub' not in tokens(p['types']):
        tags.discard('nightclub')
    if 'food' in tags and 'art' in tags and 'museum' not in tokens(p['types']):
        tags.discard('art')
    if ('trail' in low or 'hiking' in low) and ('nature' in tags or 'sightseeing' in tags):
        tags.add('hiking'); tags.add('nature')
    if 'museum store' in low or 'gift shop' in low:tags.discard('museum')
    return tags


def main_group(p):
    t=p['tags']; low=p['name'].lower()
    if 'theme_park' in t:return 'major'
    if 'food' in t:
        return 'meal'
    if 'nightclub' in t or 'live_music' in t:return 'evening'
    if 'museum' in t or 'art' in t or 'history' in t:return 'culture'
    if 'beach' in t or 'nature' in t or 'hiking' in t:return 'outdoors'
    if 'sightseeing' in t:return 'sights'
    if 'shopping' in t:return 'shopping'
    if 'nightlife' in t:return 'evening'
    return 'other'


def persona_score(p,u):
    activity_names={str(v['name']).lower():float(v.get('weight') or 0.5)
                    for v in p.get('matches',[]) if v.get('name')}
    uvec=u['activities']
    dot=sum(uvec.get(a,0)*w for a,w in activity_names.items())
    norm_u=math.sqrt(sum(v*v for v in uvec.values()))
    norm_p=math.sqrt(sum(v*v for v in activity_names.values()))
    cosine=dot/(norm_u*norm_p) if norm_u>0 and norm_p>0 else 0.0
    strength=max((uvec[a]*w for a,w in activity_names.items() if a in uvec),default=0)
    direct=max((w for c,w in u['interests'].items()
                if (c in p['tags'] or (c=='culture' and p['group']=='culture')
                    or (c=='sports' and p['group']=='outdoors'))),default=0.0)
    return min(100,int(round(max(70*cosine+20*strength,45*direct))))


def iconicity(name,city):
    n=name.lower()
    return (30 if any(n==v.lower() for v in CALIFORNIA_ANCHORS.get(city,[]))
                else 15 if city=='Los Angeles' and n in ('disneyland park','universal studios hollywood') else 0)


def duration(p):
    if p['group']=='major':return 420
    if 'hiking' in p['tags']:return 145
    if p['group']=='culture':return 120
    if p['group']=='outdoors':return 105
    if p['group']=='sights':return 65
    if p['group']=='meal':return 130 if 'fine_dining' in p['tags'] else 65
    if 'nightclub' in p['tags']:return 150
    if p['group']=='evening':return 110
    return 70

raw_places=[p for p in raw_places if not str(p.get('id','')).startswith(('GUIDED:','CURATED:'))]

LA_THEME_PARK_REQUEST = any(canonical(w['theme'])=='theme_park' or
                            (w.get('named_place') and 'disneyland' in w['named_place'].lower())
                            for w in trip['wishes'])
if 'Los Angeles' in centers and LA_THEME_PARK_REQUEST and not any(
        'disneyland' in str(p.get('name','')).lower() for p in raw_places):
    raw_places.append({
        'id':'CURATED:DISNEYLAND_PARK', 'name':'Disneyland Park',
        'address':'1313 Disneyland Drive, Anaheim, CA 92802',
        'lat':33.8120918, 'lon':-117.9189742,
        'types':'amusement_park;theme_park;tourist_attraction',
        'categories':['theme_park'], 'matches':[],
        'map_url':'https://disneyland.disney.go.com/destinations/disneyland/',
        'data_source':'supplemental_official_venue_reference'
    })
    print('Added a clearly labeled non-KG Disneyland candidate for LA-area comparison.')


def is_prohibited(p,city=None):
    names=' '.join((str(p.get('name','')),str(p.get('id','')))).lower()
    if any(v in names for v in trip.get('prohibited_places',[])):return True
    bans={canonical(x) for x in trip.get('prohibited_themes',[])}
    tags=set(p.get('tags') or classify(p))
    group=p.get('group') or main_group(p)
    for theme in bans:
        if theme=='theme_park' and (group=='major' or 'theme_park' in tags):return True
        if theme=='museum' and 'museum' in tags:return True
        if theme=='nightclub' and 'nightclub' in tags:return True
        if theme=='hiking' and 'hiking' in tags:return True
        if theme in tags:return True
    return False


def inferred_self_guided_fit(p,user):
    terms=set(p.get('tags') or [])
    expansions={'beach':('beach','ocean','sea','coast','outdoor','nature','photography'),
      'hiking':('hike','hiking','walk','trail','nature','adventure','outdoor'),
      'neighborhoods':('walk','urban','city','sightsee','culture','shopping','food'),
      'shopping':('shop','fashion','retail','market','mall'),
      'sightseeing':('photography','travel','sightseeing','architecture','history')}
    wanted=set(terms)
    for tag in terms:wanted.update(expansions.get(tag,()))
    signals=list(user.get('activities',{}).items())+list(user.get('interests',{}).items())
    values=[float(v) for phrase,v in signals if any(k in str(phrase).lower() for k in wanted)]
    return min(30,round(24*max(values,default=0)))

DEFAULT_CITY_EXPLORATION={
    'San Francisco':{'SF_NORTH_BEACH','SF_EMBARCADERO'},
    'Los Angeles':{'LA_LITTLE_TOKYO','LA_ABBOT_KINNEY'},
    'San Diego':{'SD_GASLAMP','SD_BALBOA'},
    'Napa':{'NAPA_RIVERFRONT','NAPA_OXBOW'},
    'Yosemite Valley':{'YOSEMITE_MEADOW','YOSEMITE_MIRROR'},
    'Monterey':{'MONTEREY_COAST'},
    'Santa Barbara':{'SB_WATERFRONT'},
    'Santa Cruz':{'SC_WEST_CLIFF'},
}
curated_added=[]
for item in SELF_GUIDED_CATALOG:
    eid,city,name,lat,lon,rawtypes,minutes,address=item
    if city not in centers:continue
    requested=any(canonical(w['theme']) in (
        {'beach','nature','hiking','neighborhoods','shopping','sightseeing'} &
        set().union(*(TYPE_TAGS.get(t,set()) for t in rawtypes.split(';'))))
        for w in trip['wishes'])
    suggested=eid in trip['suggested_self_guided_ids']
    default_public=eid in DEFAULT_CITY_EXPLORATION.get(city,set())
    if not (suggested or requested or default_public):continue
    synthetic={'id':'GUIDED:'+eid,'name':name,'lat':lat,'lon':lon,'address':address,
               'types':rawtypes,'categories':[],'matches':[],
               'map_url':f'https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=15/{lat}/{lon}',
               'data_source':'curated_self_guided', 'self_guided':True,
               'curated_duration':minutes,'curated_id':eid,
               'curated_llm_suggested':suggested}
    synthetic['tags']=classify(synthetic);synthetic['group']=main_group(synthetic)
    if not is_prohibited(synthetic,city):
        raw_places.append(synthetic);curated_added.append(eid)
print('Geolocated self-guided candidates:',curated_added)

all_places=[]
for p in raw_places:
    if clean_name_city(p.get('name'))=='napa valley wine train' and 'wine train' not in USER_PROMPT.lower():
        continue
    if not p['id'] or not p['name'] or p['lat'] is None or p['lon'] is None:continue
    p['lat']=float(p['lat']);p['lon']=float(p['lon'])
    p.setdefault('data_source','Neo4j')
    p['tags']=classify(p);p['group']=main_group(p)
    p['fit']={uid:(inferred_self_guided_fit(p,users[uid]) if p.get('self_guided') else persona_score(p,users[uid])) for uid in profile_ids}
    p['duration']=p.get('curated_duration',duration(p))
    p['google']={}
    if not is_prohibited(p):all_places.append(p)


def is_wish_match(p,wish,city):
    if not wish_city_eligible(p,wish,city):return False
    theme=canonical(wish['theme'])
    if wish.get('named_place'):
        target=wish['named_place'].lower()
        if target not in p['name'].lower():return False
    if theme in ('nightclub','nightlife','live_music'):
        return p['group']=='evening' and theme in p['tags']
    if theme=='theme_park':return p['group']=='major' and theme in p['tags']
    if theme in ('museum','art'):
        return p['group']=='culture' and theme in p['tags']
    if theme in ('beach','nature','hiking'):
        return p['group']=='outdoors' and theme in p['tags']
    if theme=='fine_dining':return p['group']=='meal' and theme in p['tags']
    if theme in ('sightseeing','architecture','neighborhoods','photography'):
        return p['group'] in ('sights','culture','outdoors') and (
            'sightseeing' in p['tags'] or 'history' in p['tags'])
    return theme in p['tags']


def rank_seed(p,city):
    wants=sum(1 for w in trip['wishes'] if is_wish_match(p,w,city))
    affinities=sorted(p['fit'].values())
    avg=statistics.mean(affinities) if affinities else 0
    weak=any(word in p['name'].lower() for word in
             ('dog park','playground','mini park','neighborhood park','museum store'))
    return (1.1*avg+32*min(wants,2)+iconicity(p['name'],city)
            +(26 if p.get('curated_llm_suggested') else 12 if p.get('self_guided') else 0)
            -(28 if weak and not wants else 0))

CITY_RADIUS={'San Francisco':32,'Los Angeles':68,'San Diego':37,'Anaheim':28,
             'Yosemite Valley':55,'Monterey':40}
city_pools={}
for city,(lat,lon) in centers.items():
    nearby=[]
    for p in all_places:
        km=haversine(lat,lon,p['lat'],p['lon'])
        if km <= CITY_RADIUS.get(city,35):
            candidate=dict(p,city=city,km_to_center=km)
            candidate['seed_score']=rank_seed(candidate,city)-0.15*km
            nearby.append(candidate)
    nearby.sort(key=lambda p:(-p['seed_score'],p['id']))
    unique_nearby=[];seen_nearby_ids=set()
    for candidate in nearby:
        if candidate['id'] in seen_nearby_ids:continue
        unique_nearby.append(candidate);seen_nearby_ids.add(candidate['id'])
    nearby=unique_nearby
    target=min(CANDIDATES_PER_CITY,len(nearby))
    pool=[];used=set();bucket_count=Counter()
    first_pass_caps={'meal':min(3,max(1,target//10)),
                     'culture':max(2,target//4),'sights':max(2,target//4),
                     'outdoors':max(2,target//4),'evening':min(3,max(1,target//10)),
                     'major':2,'shopping':2,'other':1}
    for p in nearby:
        if len(pool)>=target:break
        if bucket_count[p['group']]>=first_pass_caps.get(p['group'],2):continue
        pool.append(p);used.add(p['id']);bucket_count[p['group']]+=1
    for p in nearby:
        if len(pool)>=target:break
        if p['id'] in used:continue
        if p['group']=='meal' and bucket_count['meal']>=max(3,math.ceil(target*.18)):
            continue
        if p['group']=='other' and bucket_count['other']>=max(1,target//10):
            continue
        pool.append(p);used.add(p['id']);bucket_count[p['group']]+=1
    for w in trip['wishes']:
        matches=[p for p in nearby if is_wish_match(p,w,city)]
        desired=(min(3,w.get('desired_count',1)) if canonical(w['theme'])=='beach'
                 else 2 if canonical(w['theme'])=='theme_park' and not w.get('named_place')
                 else 1)
        current=sum(is_wish_match(p,w,city) for p in pool)
        for candidate in matches:
            if current>=desired:break
            if candidate['id'] in used:continue
            protected=lambda q:any(is_wish_match(q,other,city) for other in trip['wishes'])
            replacement=next((q for q in reversed(pool) if not protected(q)),None)
            if replacement is None and not w['mandatory']:
                break
            if replacement:
                pool.remove(replacement);used.discard(replacement['id'])
                bucket_count[replacement['group']]-=1
            pool.append(candidate);used.add(candidate['id'])
            bucket_count[candidate['group']]+=1;current+=1
        if not matches and (w['mandatory'] or w.get('named_place')):
            print(f'{city}: No candidate for named/mandatory wish {w["theme"]} ({w.get("named_place")}).')
    guided=[p for p in nearby if p.get('self_guided') and
            (p.get('curated_llm_suggested') or any(is_wish_match(p,w,city) for w in trip['wishes']))]
    guided=sorted(guided,key=lambda p:-p['seed_score'])[:min(6,max(2,target//5))]
    for candidate in guided:
        if candidate['id'] in used:continue
        replace=next((q for q in reversed(pool) if not q.get('self_guided') and
                      not any(is_wish_match(q,w,city) for w in trip['wishes'])),None)
        if replace is None:continue
        pool.remove(replace);used.remove(replace['id'])
        bucket_count[replace['group']]-=1
        pool.append(candidate);used.add(candidate['id'])
        bucket_count[candidate['group']]+=1

    if city=='Los Angeles' and LA_THEME_PARK_REQUEST:
        for name in ('Universal Studios Hollywood','Disneyland Park'):
            competitor=next((p for p in nearby if p['name'].lower()==name.lower()),None)
            if competitor and competitor['id'] not in used:
                replacement=next((q for q in reversed(pool)
                                  if q['group']!='major' and
                                  not any(is_wish_match(q,w,city) for w in trip['wishes'])),None)
                if replacement is None:
                    replacement=next((q for q in reversed(pool) if q['group']!='major'),None)
                if replacement:
                    pool.remove(replacement);used.discard(replacement['id'])
                    bucket_count[replacement['group']]-=1
                pool.append(competitor);used.add(competitor['id'])
                bucket_count[competitor['group']]+=1
    if not pool:
        raise RuntimeError(f'No California KG places near {city}. Check destination or KG coordinates.')
    city_pools[city]=pool
    print(f'{city}: {len(nearby)} nearby in KG, {len(pool)}/{target} shortlisted. '
          f'Mix: {dict(Counter(p["group"] for p in pool))}')

from difflib import SequenceMatcher
from opening_hours import OpeningHours

def clean_name(s):
    return re.sub(r'[^a-z0-9]+',' ',str(s or '').lower()).strip()

geoapify_errors=[]
geoapify_counts=Counter()


# Source-backed, high-impact public hours. Keys are normalized exact official place
# names (aliases allowed). These are TYPICAL published hours, not date-specific tickets.
# More restrictive information wins: an official closure is never overwritten by
# Geoapify or the generic assumed-hours fallback.
OFFICIAL_HOURS={
  'the broad':dict(weekly={1:[(660,1020)],2:[(660,1020)],3:[(660,1200)],
      4:[(660,1020)],5:[(600,1080)],6:[(600,1080)]},
      closed_holidays=('christmas','thanksgiving'),
      url='https://www.thebroad.org/visit'),
  'san francisco museum of modern art':dict(weekly={0:[(600,1020)],1:[(600,1020)],
      3:[(720,1200)],4:[(600,1020)],5:[(600,1020)],6:[(600,1020)]},
      closed_holidays=('christmas','thanksgiving'),url='https://www.sfmoma.org/visit/'),
  'exploratorium':dict(weekly={1:[(600,1020)],2:[(600,1020)],3:[(600,1020)],
      4:[(600,1020)],5:[(600,1020)],6:[(720,1020)]},
      # Thursday's adult-only After Dark programming is not a general admission slot.
      url='https://www.exploratorium.edu/visit'),
  'rich table':dict(weekly={i:[(1020,1320)] for i in range(7)},
      closed_holidays=('christmas','thanksgiving','new_years_day'),
      url='https://www.richtablesf.com/location/rich-table/'),
  'uss midway museum':dict(weekly={i:[(600,1020)] for i in range(7)},
      latest_start=960,closed_holidays=('christmas','thanksgiving'),
      url='https://www.midway.org/contact/contact-us'),
  'cabrillo national monument':dict(weekly={i:[(540,1020)] for i in range(7)},
      closed_holidays=('christmas','thanksgiving'),
      url='https://home.nps.gov/cabr/planyourvisit/hours.htm'),
  'napa valley wine train':dict(weekly={i:[(1100,1320)] for i in range(7)},
      fixed_start=1100, duration=170, dated_service_unverified=True,
      url='https://www.winetrain.com/our-experiences/evening-journeys/'),
}
OFFICIAL_ALIASES={'sfmoma':'san francisco museum of modern art',
                  'san francisco museum of modern art sfmoma':'san francisco museum of modern art',
                  'uss midway':'uss midway museum',
                  'napa valley wine train station':'napa valley wine train'}

def official_rule(p):
    name=clean_name(p['name'])
    key=OFFICIAL_ALIASES.get(name,name)
    return OFFICIAL_HOURS.get(key)

def thanksgiving(d):
    return d.month==11 and d.weekday()==3 and 22<=d.day<=28

def is_official_holiday(d,holiday):
    return (holiday=='christmas' and (d.month,d.day)==(12,25)
      or holiday=='thanksgiving' and thanksgiving(d)
      or holiday=='new_years_day' and (d.month,d.day)==(1,1))

def published_windows(p,day):
    r=official_rule(p)
    if r is None:return None
    if any(is_official_holiday(day,h) for h in r.get('closed_holidays',())):
        return [],'official_closed'
    return r['weekly'].get(day.weekday(),[]),'official_typical'

def geoapify_hours(p):
    """Coordinate-based lookup; NEVER pass a Google placeId to Geoapify."""
    data=get_json(GEOAPIFY_DETAILS_URL,params={
        'lat':round(p['lat'],6),'lon':round(p['lon'],6),
        'apiKey':GEOAPIFY_KEY
    },attempts=1)
    for feature in data.get('features',[]):
        props=feature.get('properties') or {}
        if props.get('feature_type') not in ('details',None): continue
        hrs=props.get('opening_hours')
        nm=props.get('name') or ''
        if not hrs or not nm:continue
        similarity=SequenceMatcher(None,clean_name(p['name']),clean_name(nm)).ratio()
        lat=props.get('lat');lon=props.get('lon')
        dist=(haversine(p['lat'],p['lon'],lat,lon)*1000 if lat is not None and lon is not None
              else float('inf'))
        if similarity>=GEOAPIFY_MIN_NAME_MATCH and dist<=GEOAPIFY_MAX_DISTANCE_M:
            # Validate OSM-hours syntax before using it in an actual tour.
            try:
                OpeningHours(hrs,timezone=ZoneInfo(TIMEZONE),country='US')
            except Exception:
                geoapify_counts['unparseable_hours']+=1
                continue
            p['geoapify_hours']=hrs
            p['geoapify_match']={'name':nm,'similarity':round(similarity,3),
                                 'distance_m':round(dist)}
            return True
        geoapify_counts['rejected_identity_match']+=1
    return False

if GEOAPIFY_KEY:
    stop_provider=False
    for city,pool in city_pools.items():
        for p in pool[:GEOAPIFY_REQUEST_LIMIT_PER_CITY]:
            if stop_provider:break
            if official_rule(p) or p.get('self_guided'):continue
            try:
                geoapify_counts['requests']+=1
                if geoapify_hours(p):geoapify_counts['matched']+=1
            except Exception as exc:
                geoapify_errors.append({'city':city,'place':p['name'],'error':str(exc)[:150]})
                geoapify_counts['errors']+=1
                if any(f'HTTP {code}' in str(exc) for code in (401,403,429)):
                    print('Geoapify authorization or quota problem; stopping further lookups.')
                    stop_provider=True
        print(f'{city}: Geoapify matched {sum(bool(x.get("geoapify_hours")) for x in pool)}'
              f' of {min(len(pool),GEOAPIFY_REQUEST_LIMIT_PER_CITY)} attempted places')
        if stop_provider:break
else:
    print('No Geoapify key; using explicit assumed-hour windows throughout.')

# Legacy variables are kept for downstream evaluation compatibility; no calls occur.
osm_lookup_errors=[];google_errors=[]
for city,pool in city_pools.items():
    for p in pool:
        p['google']={}
        p['tags']=classify(p);p['group']=main_group(p)
        p['fit']={uid:(inferred_self_guided_fit(p,users[uid]) if p.get('self_guided') else persona_score(p,users[uid])) for uid in profile_ids}
        p['duration']=p.get('curated_duration',duration(p))
        if rule := official_rule(p):
            p['duration']=rule.get('duration',p['duration'])
        p['quality']=0
        p['iconic']=iconicity(p['name'],city)

def osm_windows(expression,target_day):
    """Parse typical weekly OSM hours using a tested library, not ad-hoc regex."""
    hours=OpeningHours(expression,timezone=ZoneInfo(TIMEZONE),country='US')
    base=datetime.combine(target_day,datetime.min.time()).replace(tzinfo=ZoneInfo(TIMEZONE))
    bins=[]
    for minute in range(0,1440,15):
        if hours.is_open(base+timedelta(minutes=minute+7)):
            bins.append(minute)
    if not bins:return []
    windows=[];start=prev=bins[0]
    for m in bins[1:]:
        if m!=prev+15:
            windows.append((start,prev+15));start=m
        prev=m
    windows.append((start,prev+15))
    return windows

def place_windows(p,date_text):
    day=date.fromisoformat(date_text)
    if p.get('self_guided'):
        # Planning daylight/urban window, not a public venue's claimed opening times.
        return [(8*60,19*60)],'self_guided_assumed_access'
    sourced=published_windows(p,day)
    if sourced is not None:return sourced
    if p.get('geoapify_hours'):
        try:
            return osm_windows(p['geoapify_hours'],day),'geoapify_typical'
        except Exception:
            pass
    # Unknown opening hours are not treated as permanently closed.
    # These are conservative, PROVISIONAL planning assumptions, not factual hours.
    group=p['group']
    if group=='meal':
        return [(11*60,14*60+30),(17*60,22*60)],'assumed'
    if group=='evening':return [(18*60,24*60)],'assumed'
    if group=='major':return [(9*60,19*60)],'assumed'
    if group=='culture':return [(10*60,17*60)],'assumed'
    if group=='shopping':return [(10*60,20*60)],'assumed'
    if group=='outdoors':return [(7*60,19*60)],'assumed'
    return [(9*60,19*60)],'assumed'


def weather_city(city, relevant_dates):
    lat,lon=centers[city]
    answer={}
    try:
        forecast=get_json(FORECAST_URL,params={
            'latitude':lat,'longitude':lon, 'timezone':TIMEZONE,'forecast_days':16,
            'daily':'temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max'
        })['daily']
        for i,ds in enumerate(forecast['time']):
            if ds not in relevant_dates:continue
            value=lambda key: forecast.get(key,[None]*len(forecast['time']))[i]
            answer[ds]={'source':'forecast','high_c':value('temperature_2m_max'),
                'low_c':value('temperature_2m_min'),
                'rain_probability_pct':value('precipitation_probability_max'),
                'rain_mm':value('precipitation_sum')}
    except Exception as exc:print(city,'forecast unavailable:',str(exc)[:140])
    missing=[ds for ds in relevant_dates if ds not in answer]
    if missing:
        # Eight complete preceding years, same-season +/- 10 calendar days.
        end_year=TODAY.year-1
        first_year=max(2018,end_year-7)
        try:
            archive=get_json(ARCHIVE_URL,params={
                'latitude':lat,'longitude':lon,'timezone':TIMEZONE,
                'start_date':f'{first_year}-01-01','end_date':f'{end_year}-12-31',
                'daily':'temperature_2m_max,temperature_2m_min,precipitation_sum'
            })['daily']
            hist=[]
            for i,stamp in enumerate(archive.get('time',[])):
                values=[archive.get(k,[]) for k in ('temperature_2m_max','temperature_2m_min','precipitation_sum')]
                if all(len(v)>i and v[i] is not None for v in values):
                    hist.append((date.fromisoformat(stamp),values[0][i],values[1][i],values[2][i]))
            for ds in missing:
                target=date.fromisoformat(ds)
                samples=[]
                for day,hi,lo,rain in hist:
                    # compare month/day in fixed leap year (handles Dec/Jan properly)
                    day_pos=date(2000,day.month,day.day).timetuple().tm_yday
                    target_pos=date(2000,target.month,target.day).timetuple().tm_yday
                    offset=abs(day_pos-target_pos)
                    if min(offset,366-offset)<=10:samples.append((hi,lo,rain))
                if samples:
                    answer[ds]={
                        'source':'seasonal_history_NOT_forecast',
                        'historical_years':f'{first_year}-{end_year}',
                        'typical_high_c':round(statistics.mean(x[0] for x in samples),1),
                        'typical_low_c':round(statistics.mean(x[1] for x in samples),1),
                        'historical_wet_day_frequency_pct':round(100*sum(x[2]>=1 for x in samples)/len(samples)),
                    }
        except Exception as exc:
            print(city,'archive unavailable:',str(exc)[:140])
    for ds in missing:
        answer.setdefault(ds,{'source':'unknown'})
    return answer

weather={}
for city in centers:
    relevant=[d['date'] for d in days if d['city']==city]
    for ds,datum in weather_city(city,relevant).items():
        weather[(city,ds)]=datum
print('Weather coverage:',Counter(x['source'] for x in weather.values()))

def ors_matrix(locations,mode):
    if not ORS_KEY and REQUIRE_ORS:raise RuntimeError('Enter a valid ORS key.')
    payload=get_json(
        ORS_MATRIX_URL+mode,
        method='post',
        headers={'Authorization':ORS_KEY,'Content-Type':'application/json'},
        body={'locations':[[float(lon),float(lat)] for lat,lon in locations],
              'metrics':['duration','distance'],'units':'m'},
    )
    durations=payload.get('durations');distances=payload.get('distances')
    if not durations or not distances:
        raise RuntimeError(f'ORS {mode} matrix incomplete: {str(payload)[:250]}')
    return durations,distances

routes={};route_modes={}
for city,pool in city_pools.items():
    home=HOTELS.get(city,centers[city])
    pts=[{'id':f'BASE:{city}','lat':home[0],'lon':home[1]}]+pool
    coords=[(p['lat'],p['lon']) for p in pts]
    walk_d,walk_m=ors_matrix(coords,'foot-walking')
    drive_d,drive_m=ors_matrix(coords,'driving-car')
    for i,a in enumerate(pts):
        for j,b in enumerate(pts):
            if i==j:continue
            walk=walk_d[i][j];metres=walk_m[i][j]
            drive=drive_d[i][j]
            if walk is not None and metres is not None and metres<=1750 and walk<=25*60:
                mins=max(1,math.ceil(walk/60));mode='walk'
            elif drive is not None:
                mins=max(1,math.ceil(drive/60))+8;mode='drive + 8-min pickup buffer'
            else:continue
            routes[(a['id'],b['id'])]=mins
            route_modes[(a['id'],b['id'])]=mode
    print(city,'ORS reachable legs:',sum(1 for (a,b) in routes if a.startswith('BASE:'+city)))

# A POI without both a base outbound and inbound route cannot be promised feasible.
for city,pool in city_pools.items():
    hub=f'BASE:{city}'
    city_pools[city]=[p for p in pool if (hub,p['id']) in routes and (p['id'],hub) in routes]
    if not city_pools[city]:raise RuntimeError(f'ORS could not route to any shortlisted place in {city}.')

from functools import lru_cache
from astral import LocationInfo
from astral.sun import sun

@lru_cache(maxsize=512)
def sunset_minutes(city,date_text):
    lat,lon=centers[city]
    loc=LocationInfo(city,'California',TIMEZONE,lat,lon)
    sunset=sun(loc.observer,date=date.fromisoformat(date_text),
               tzinfo=ZoneInfo(TIMEZONE))['sunset']
    return sunset.hour*60+sunset.minute

# Calculate opening windows only once per POI and travel date.
window_cache={}
def allowed_windows(p,date_text,city):
    key=(p['id'],date_text,city)
    if key in window_cache:return window_cache[key]
    openings,source=place_windows(p,date_text)
    rule=official_rule(p)
    if rule and 'fixed_start' in rule:
        # Fixed published departure: normal 'sightseeing' daytime bounds do not apply.
        start=rule['fixed_start'];bounds=[(start,start+p['duration'])]
    elif p['group']=='meal':
        bounds=[(11*60+30,14*60+30),(17*60+30,22*60)]
        if 'fine_dining' in p['tags']:bounds=[(17*60,22*60)]
    elif p['group']=='evening':bounds=[(18*60,24*60)]
    elif p['group']=='outdoors':bounds=[(7*60,max(7*60,sunset_minutes(city,date_text)-25))]
    elif p['group']=='major':bounds=[(9*60,19*60)]
    else:bounds=[(9*60,19*60)]
    windows=[]
    for a,b in openings:
        for c,d in bounds:
            low,high=max(a,c),min(b,d)
            if high-low>=p['duration']:windows.append((low,high))
    result=(sorted(set(windows)),source)
    window_cache[key]=result
    return result


def plan_day(order,day,prev_late=False):
    """Schedule ordered places using ORS legs and venue opening windows."""
    city=day['city'];base=f'BASE:{city}'
    natural_early=(trip['nature_start_hour']*60 if city=='Yosemite Valley' else
                   trip['city_start_hour']*60)
    ready=(15*60 if day['transfer'] else max(11*60+30,natural_early) if prev_late else natural_early)
    pos=base;stops=[];distance_time=0;wait=0
    for p in order:
        leg=routes.get((pos,p['id']))
        if leg is None:return None
        windows,source=allowed_windows(p,day['date'],city)
        arrival=ready+leg
        valid=[max(arrival,a) for a,b in windows
               if max(arrival,a)+p['duration']<=b]
        if not valid:return None
        rule=official_rule(p)
        if rule and 'fixed_start' in rule:
            required=rule['fixed_start']
            valid=[required] if any(a<=required and required+p['duration']<=b
                 and arrival<=required for a,b in windows) else []
            if not valid:return None
        if rule and 'latest_start' in rule:
            valid=[candidate for candidate in valid if candidate<=rule['latest_start']]
            if not valid:return None
        start=min(valid)
        if start+p['duration']>24*60:return None
        wait+=start-arrival;distance_time+=leg
        stops.append({'place_id':p['id'],'name':p['name'],'start_min':start,
            'end_min':start+p['duration'],'category':p['group'],
            'themes':sorted(p['tags']),'address':p['address'],
            'maps_url':p['map_url'],'hours_basis':source,
            'route_in_minutes':leg,'route_mode':route_modes.get((pos,p['id']),'unknown'),
            'data_source':p.get('data_source','Neo4j'),
            'official_hours_url':(official_rule(p) or {}).get('url'),
            'suitable_for':[uid for uid,v in p['fit'].items() if v>=8]})
        ready=start+p['duration'];pos=p['id']
    back=routes.get((pos,base))
    if back is None:return None
    distance_time+=back
    max_end=(24*60+30 if order[-1]['group']=='evening' else 23*60+30)
    if ready+back>max_end:return None
    return {'activities':stops,'travel_minutes':distance_time,
            'waiting_minutes':wait,'route_cost':distance_time+max(0,wait-90)*.6,
            'late_finish':bool(stops[-1]['end_min']>=23*60),
            'first_start':stops[0]['start_min']}


def composition_ok(path,addition,day):
    group=addition['group']
    counts=Counter(p['group'] for p in path)
    if counts[group]>=(1 if group in ('meal','evening','major') else
                       2 if group=='culture' else 3):return False
    if group=='major' and path:return False
    if counts['major']:return False
    if day['transfer'] and addition['duration']>210:return False
    if sum(p['duration'] for p in path)+addition['duration']>(350 if day['transfer'] else 730):
        return False
    return True


def tour_value(path,plan,day):
    # This score only ranks feasible candidate tours for beam search.
    mean=sum(sum(p['fit'].values())/max(1,len(profile_ids)) for p in path)
    req=sum(any(is_wish_match(p,w,day['city']) for p in path)
            for w in trip['wishes'])
    icon=sum(p.get('iconic',0) for p in path)
    categories=len({p['group'] for p in path})
    # Per-day scoring is a BEAM-SEARCH heuristic only; user satisfaction and fairness
    # are measured across the entire trip in CP-SAT, not enforced per day.
    # A nice full day beats an isolated minor stop; do not cram a full-day theme park.
    substantial=sum(p['group'] not in ('meal','shopping','evening') for p in path)
    composition=35*min(substantial,2) + (12 if len(path)>=3 else 0)
    if day['transfer']:composition=10*min(len(path),2)
    if any(p['group']=='major' for p in path):composition=max(composition,75)
    if not day['transfer'] and not any(p['group']=='major' or
       ('hiking' in p['tags'] and p['duration']>=180) for p in path):
        if substantial==0:composition-=150
        elif substantial==1:composition-=70
        elif substantial>=2:composition+=105
    if len(path)==1 and path[0]['group'] in ('meal','shopping','evening'):composition-=130
    return mean+32*req+1.8*icon+11*categories+7*len(path)+composition-.28*plan['route_cost']


def build_day_options(day,pool):
    # Explicit requests are scored up, but nothing from a PREVIOUS prompt is inherited.
    # Preserve competing theme parks, coastline and shopping/walking experiences.
    ranked=sorted(pool,key=lambda p:-(p['seed_score']+20*sum(
        is_wish_match(p,w,day['city']) for w in trip['wishes'])))
    shortlist=ranked[:TOUR_TOP_CANDIDATES]
    for w in trip['wishes']:
        keep=2 if canonical(w['theme'])=='theme_park' and not w.get('named_place') else 1
        for match in [p for p in ranked if is_wish_match(p,w,day['city'])][:keep]:
            if match['id'] not in {x['id'] for x in shortlist}:
                shortlist.append(match)
    max_n=MAX_STOPS_TRANSFER_DAY if day['transfer'] else MAX_STOPS_FULL_DAY
    beam=[tuple()]
    candidates={}
    for depth in range(1,max_n+1):
        expanded=[]
        for path in beam:
            used={p['id'] for p in path}
            for p in shortlist:
                if p['id'] in used or not composition_ok(path,p,day):continue
                order=path+(p,)
                # Consider both regular mornings and sleep-in variants. The solver
                # later picks a compatible next-day tour after an evening finish.
                for late_start in (False,True) if not day['transfer'] else (False,):
                    result=plan_day(order,day,prev_late=late_start)
                    if result is None:continue
                    score=tour_value(order,result,day)
                    signature=(tuple(x['id'] for x in order),result['first_start'])
                    item={'path':order,'plan':result,'score':score,
                          'place_ids':tuple(x['id'] for x in order),
                          'utility':{uid:sum(x['fit'][uid] for x in order)
                                     for uid in profile_ids},
                          'groups':frozenset(x['group'] for x in order),
                          'iconic':sum(x.get('iconic',0) for x in order),
                          'matched_wishes':{i:sum(is_wish_match(x,w,day['city'])
                                                   for x in order)
                                            for i,w in enumerate(trip['wishes'])}}
                    old=candidates.get(signature)
                    if old is None or score>old['score']:
                        candidates[signature]=item
                    if not late_start:
                        expanded.append(item)
        if not expanded:break
        expanded.sort(key=lambda t:(-t['score'],t['place_ids']))
        # Limit expansion cost but preserve a few tours anchored on explicit wishes.
        kept=expanded[:TOUR_BEAM_WIDTH]
        for wi,w in enumerate(trip['wishes']):
            fitting=next((t for t in expanded if t['matched_wishes'].get(wi,0)),None)
            if fitting and fitting not in kept:kept.append(fitting)
        beam=[x['path'] for x in kept]
    options=sorted(candidates.values(),key=lambda t:(-t['score'],t['place_ids']))
    # Preserve the strongest tours, but leave room for geographically distinct
    # alternatives. A top-K comprised of variations around the same famous POIs
    # becomes impossible to spread over a week once repeat visits are forbidden.
    kept=options[:DAY_OPTION_LIMIT]
    for t in options:
        keep=(len(t['path'])==1 or
              any((w['mandatory'] or w.get('named_place')) and
                  t['matched_wishes'].get(i,0)
                  for i,w in enumerate(trip['wishes'])))
        if keep and t not in kept:kept.append(t)
    # Don't let the top-L cap consist only of one or two closely related motifs.
    # Preserve the best feasible multi-stop option for each broad composition.
    for t in options:
        if len(t['path'])>=3 and t['groups'] not in [v['groups'] for v in kept]:
            kept.append(t)
        if len(kept)>DAY_OPTION_LIMIT+35:break
    # v7.5.1: coverage-preserving full-day alternatives. Top scoring tours
    # frequently use the same 4-5 famous venues, so when CP-SAT disallows
    # repeats across days it is left with singleton meals as its only choices.
    # Retain feasible 2+ daytime stops anchored on *each* distinct candidate.
    # This is a modest expansion of the original options, not a second planner.
    if not day['transfer']:
        key=lambda t:(-t['score'],t['place_ids'])
        substantial=lambda t:sum(p['group'] not in ('meal','shopping','evening')
                                  for p in t['path'])
        usable=[t for t in options if substantial(t)>=2 and
                not any(p['group']=='major' for p in t['path'])]
        # At least one well-composed option with every viable POI.
        # Duplicate tour shapes will be removed below.
        for anchor in shortlist:
            if anchor['group'] in ('meal','shopping','evening','major'):
                continue
            champion=next((t for t in usable if anchor['id'] in t['place_ids']),None)
            if champion is not None:kept.append(champion)
        # Preserve varied activity pairings, not only permutations of the same
        # cultural sights. This is critical for a 7+ night city stay.
        best_by_pair={}
        for t in usable:
            pair=tuple(sorted(t['groups']))
            if pair not in best_by_pair or t['score']>best_by_pair[pair]['score']:
                best_by_pair[pair]=t
        kept.extend(best_by_pair.values())

    # Remove exact duplicates in selected paths and departure time.
    seen=set();unique=[]
    for t in kept:
        sig=(t['place_ids'],t['plan']['first_start'])
        if sig not in seen:unique.append(t);seen.add(sig)
    return unique

by_day={i:city_pools[day['city']] for i,day in enumerate(days)}
day_options={}
for d,day in enumerate(days):
    opts=build_day_options(day,by_day[d])
    if not opts:
        raise RuntimeError(f'{day["city"]} on {day["date"]}: zero routable stops. '
                           'Check ORS coverage, POI opening data and hotel/base coordinates.')
    day_options[d]=opts
    print(f'{day["date"]} {day["city"]}: {len(opts)} route-and-time-feasible daily alternatives')


# Select one complete, already feasible tour per day.
# No follow-on scheduler is allowed to change the result.
model=cp_model.CpModel()
select={}
for d,options in day_options.items():
    xs=[]
    for j,opt in enumerate(options):
        x=model.new_bool_var(f'tour_{d}_{j}')
        select[(d,j)]=x;xs.append(x)
    model.add(sum(xs)==1)


# Prevent a supplemental self-guided anchor and a near-identical KG place from
# appearing as two different experiences within one vacation.
near_duplicate_pairs=set()
for city,pool in city_pools.items():
    guide=[p for p in pool if p.get('self_guided')]
    for p in guide:
        for q in pool:
            if q['id']==p['id'] or q.get('self_guided'):continue
            if (haversine(p['lat'],p['lon'],q['lat'],q['lon'])<0.18 and
                len(p['tags'] & q['tags'])>=2):
                near_duplicate_pairs.add(tuple(sorted((p['id'],q['id']))))

# No duplicate attraction across different days.
for place_id in {pid for options in day_options.values() for t in options
                 for pid in t['place_ids']}:
    model.add(sum(select[(d,j)] for d,options in day_options.items()
                  for j,t in enumerate(options) if place_id in t['place_ids'])<=1)


for left,right in sorted(near_duplicate_pairs):
    pair_selections=[select[(d,j)] for d,opts in day_options.items()
                     for j,t in enumerate(opts)
                     if left in t['place_ids'] or right in t['place_ids']]
    if pair_selections:model.add(sum(pair_selections)<=1)

# A late finish only allows a compatible late-start tour the next day.
if trip['night_owl_recovery']:
    for d in range(1,len(days)):
        prev_late=[select[(d-1,j)] for j,t in enumerate(day_options[d-1])
                   if t['plan']['late_finish']]
        early=[select[(d,j)] for j,t in enumerate(day_options[d])
               if t['plan']['first_start']<11*60+30 and not days[d]['transfer']]
        if prev_late and early:
            model.add(sum(prev_late)+sum(early)<=1)

# Explicit mandatory requirements are enforced across the WHOLE trip.
unavailable_mandatory_wishes = []
for i,w in enumerate(trip['wishes']):
    if not w['mandatory']:continue
    matching=sum(t['matched_wishes'][i]*select[(d,j)]
                 for d,options in day_options.items() for j,t in enumerate(options))
    # A requested venue or theme can be absent from every feasible daily tour
    # (for example, a venue is closed or missing from the place graph). Keep
    # the rest of the trip usable and report the wish as unmet in the results.
    possible_count=sum(max((t['matched_wishes'][i] for t in options), default=0)
                       for options in day_options.values())
    if possible_count == 0:
        unavailable_mandatory_wishes.append(dict(w))
        w['mandatory'] = False
        w['minimum'] = 0
        continue
    count=min(max(1,w['minimum']), possible_count)
    model.add(matching>=count)

# At most one fine-dining night every five days; no adjacent premium dinners.
for d in range(len(days)):
    fine=sum(select[(d,j)] for j,t in enumerate(day_options[d])
             if any('fine_dining' in p['tags'] for p in t['path']))
    if d+1<len(days):
        after=sum(select[(d+1,j)] for j,t in enumerate(day_options[d+1])
                  if any('fine_dining' in p['tags'] for p in t['path']))
        model.add(fine+after<=1)
model.add(sum(select[(d,j)] for d,opts in day_options.items()
              for j,t in enumerate(opts)
              if any('fine_dining' in p['tags'] for p in t['path']))
          <=max(1,math.ceil(len(days)/5)))

# All individual scores accumulate across the WHOLE TRIP. A given day may
# favor one person; the fairness term protects against neglect over the vacation.
# Normalize modeled group preference to profile-specific attainable trip capacity.
# Sparse profiles with no signal are excluded from minimum-utility fairness,
# but still appear in the report rather than being presented as 'satisfied'.
preference_capacity={}
normalized={};fair_users=[];raw_vars={}
for uid in profile_ids:
    upper=sum(max(t['utility'][uid] for t in options)
              for options in day_options.values())
    preference_capacity[uid]=upper
    raw=model.new_int_var(0,max(1,upper),f'utility_{uid}')
    model.add(raw==sum(t['utility'][uid]*select[(d,j)]
        for d,options in day_options.items() for j,t in enumerate(options)))
    raw_vars[uid]=raw
    if upper>0:
        z=model.new_int_var(0,1000,f'fair_{uid}')
        model.add_division_equality(z,1000*raw,upper)
        normalized[uid]=z;fair_users.append(uid)
if fair_users:
    least=model.new_int_var(0,1000,'min_user_coverage')
    model.add_min_equality(least,[normalized[u] for u in fair_users])
else:least=model.new_constant(0)

# Soft wish coverage and other measured quantities.
soft=[]
for i,w in enumerate(trip['wishes']):
    if w['mandatory']:continue
    candidates=[(t['matched_wishes'][i],select[(d,j)])
                for d,options in day_options.items() for j,t in enumerate(options)
                if t['matched_wishes'][i]]
    if candidates:
        visits=sum(number*x for number,x in candidates)
        preferred=max(1,w.get('desired_count',1))
        achieved=model.new_int_var(0,preferred,f'soft_{i}')
        model.add_min_equality(achieved,[visits,model.new_constant(preferred)])
        soft.append(achieved)

G=sum(sum(t['utility'].values())*select[(d,j)]
      for d,options in day_options.items() for j,t in enumerate(options))
F=len(profile_ids)*least
C=len(profile_ids)*sum(soft) if soft else 0
I=len(profile_ids)*sum(t['iconic']*select[(d,j)]
    for d,options in day_options.items() for j,t in enumerate(options))
# Across-trip diversity: first occurrence of each category counts much more than
# repeating the same category all week. A small capped repeat reward means
# another beach day can still be good, without infinitely rewarding restaurants.
trip_groups=('sights','culture','outdoors','major','evening','shopping','meal')
seen_groups=[];repeat_groups=[]
for category in trip_groups:
    present=[select[(d,j)] for d,opts in day_options.items()
             for j,t in enumerate(opts) if category in t['groups']]
    if not present:continue
    used=model.new_bool_var(f'trip_has_{category}')
    model.add(sum(present)>=used)
    model.add(sum(present)<=len(days)*used)
    seen_groups.append(used)
    capped=model.new_int_var(0,min(3,len(days)),f'trip_repeat_{category}')
    model.add_min_equality(capped,[sum(present),model.new_constant(3)])
    repeat_groups.append(capped)
V=len(profile_ids)*(12*sum(seen_groups)+2*sum(repeat_groups))
T=sum(round(t['plan']['route_cost'])*select[(d,j)]
      for d,options in day_options.items() for j,t in enumerate(options))

# Daily pacing is a *secondary* quality term. Never require every person to
# be equally served on every day, or force a minimum stop count.
# Daily composition is an explicit objective rather than an infeasible 3-stop minimum.
# A full-day theme park counts as a substantial day; transfers may naturally be light.
D=0
for d,options in day_options.items():
    for j,t in enumerate(options):
        path=t['path']
        nonmeal=sum(p['group'] not in ('meal','shopping','evening') for p in path)
        major=any(p['group']=='major' for p in path)
        substantial_hike=any('hiking' in p['tags'] and p['duration']>=180 for p in path)
        # A relaxed pace means generous buffers, NOT an empty full day.
        # Only the explicitly marked transfer/major/long-hike cases get the
        # one-stop exception.
        rest_requested=False
        if days[d]['transfer']:
            points=30 if len(path)>=2 else 12
        elif major or substantial_hike:
            points=225
        elif nonmeal>=2 and len(path) in (2,3,4):
            points=300 + (35 if nonmeal>=3 else 0)
        elif nonmeal==1 and len(path)>=2:
            points=125
        elif rest_requested and nonmeal==1:
            points=55
        elif len(path)==1:
            points=-280 if path[0]['group'] in ('meal','shopping','evening') else -205
        else:points=-150
        # Individual days can favor different travelers: this is NOT daily fairness.
        # Discourage marathon itineraries beyond the ordinary four-stop day.
        if len(path)>4 and not major:points-=130*(len(path)-4)
        D+=points*select[(d,j)]

# v7.5.1: protect the integrity of an ordinary sightseeing day.
# No hard minimum that could make the entire 21-day problem infeasible. Instead,
# prefer a viable two-experience day to a lunch-only day whenever possible.
# This correction affects the allocation of EXPERIENCES among days; individual
# traveler satisfaction and min-user fairness remain WHOLE-TRIP quantities.
BAD_DAY=0
THIN_DAY=0
for d,options in day_options.items():
    day=days[d]
    for j,t in enumerate(options):
        path=t['path']
        if day['transfer']:continue
        if any(p['group']=='major' or
               ('hiking' in p['tags'] and p['duration']>=180) or
               (p['group']=='outdoors' and p['duration']>=240) for p in path):
            continue
        daytime=sum(p['group'] not in ('meal','shopping','evening') for p in path)
        if daytime==0:
            BAD_DAY+=select[(d,j)]
        elif daytime==1:
            THIN_DAY+=select[(d,j)]

# A weekend entertainment preference is not a hard promise of a ticketed event.
WK=0
if trip.get('weekend_nightlife'):
    for d,options in day_options.items():
        if date.fromisoformat(days[d]['date']).weekday() in (4,5):
            WK+=sum(95*select[(d,j)] for j,t in enumerate(options)
                    if any(p['group']=='evening' for p in t['path']))

model.maximize(W['group']*G+W['fairness']*F+W['request']*C
               +W['iconic']*I+W['variety']*V-W['travel']*T+W['daily']*D+WK
               -MEAL_ONLY_FULL_DAY_PENALTY*BAD_DAY
               -ONE_PRIMARY_FULL_DAY_PENALTY*THIN_DAY)
solver=cp_model.CpSolver()
solver.parameters.max_time_in_seconds=SOLVER_SECONDS
solver.parameters.num_search_workers=8
status=solver.solve(model)
solver_status=solver.status_name(status)
if status==cp_model.UNKNOWN:
    # UNKNOWN means time ran out before a usable plan, not mathematical infeasibility.
    solver.parameters.max_time_in_seconds=120
    status=solver.solve(model)
    solver_status=solver.status_name(status)
if status not in (cp_model.FEASIBLE,cp_model.OPTIMAL):
    raise RuntimeError(f'CP-SAT returned {solver_status}. If INFEASIBLE, check hard '
        'wishes, repeated-POI coverage and the amount of KG data; if UNKNOWN, the '
        'search has not found a plan within the time limit. No stops are silently dropped.')

selected={};schedule=[]
for d,day in enumerate(days):
    j=next(j for j in range(len(day_options[d])) if solver.value(select[(d,j)]))
    chosen=day_options[d][j]
    selected[d]=list(chosen['path'])
    schedule.append({**day,**chosen['plan'],
        'weather':weather.get((day['city'],day['date']),{'source':'unknown'}),
        'base_is_hotel':day['city'] in HOTELS})

# Self-guided experiences are real itinerary SEGMENTS, not fake KG POIs:
# place-anchored exploration uses unused time after a stop (or hotel-base time),
# no additional venue opening assumption, route, ticket or booking is invented.
def add_self_guided_segments(day):
    actual=day['activities']
    venues={p['id']:p for p in city_pools[day['city']]}
    result=[]
    for k,stop in enumerate(actual):
        result.append({'segment_type':'kg_venue', 'title':stop['name'],
                       'start_min':stop['start_min'],'end_min':stop['end_min'],
                       'anchor':stop['name'],'category':stop['category'],
                       'place_id':stop['place_id']})
        typ=set(stop.get('themes') or [])
        if stop['place_id'].startswith('GUIDED:') or stop['category'] not in ('sights','outdoors','shopping'):
            continue
        # Only allocate a short self-guided time if there is real unallocated
        # time AFTER this venue and BEFORE the next scheduled travel leg.
        next_stop=actual[k+1] if k+1<len(actual) else None
        end=stop['end_min']; slot=40
        if next_stop:
            available=next_stop['start_min']-next_stop['route_in_minutes']-end
        else:
            available=(18*60 if stop['category']=='outdoors' else 19*60)-end
        if available<slot+10:continue
        # Do not extend park/mall hours or outdoor daylight just to fill a gap.
        venue=venues.get(stop['place_id'])
        if venue is None:continue
        windows,_=allowed_windows(venue,day['date'],day['city'])
        if not any(a<=end and end+slot<=b for a,b in windows):continue
        if 'beach' in typ:label=f'Beach time and waterfront stroll at {stop["name"]}'
        elif stop['category']=='shopping':label=f'Browse and explore around {stop["name"]}'
        elif stop['category']=='outdoors':label=f'Explore the trails and scenery at {stop["name"]}'
        else:label=f'Self-guided walk around {stop["name"]}'
        result.append({'segment_type':'self_guided', 'title':label,
                       'start_min':end,'end_min':end+slot,
                       'anchor':stop['name'],'category':'exploration',
                       'place_id':stop['place_id'],
                       'notes':'Unbooked, self-directed time at the scheduled place; not another venue.'})
    day['segments']=sorted(result,key=lambda x:(x['start_min'],x['end_min']))

for day in schedule:
    add_self_guided_segments(day)

issues=[];reopt_attempts=0
print(f'CP-SAT: {solver_status}. {len(schedule)} feasible day tours selected '
      'without any post-selection deletion or repair.')


# Validate all final constraints: do not change what the optimizer selected.
planned=[stop for day in schedule for stop in day['activities']]
ids=[s['place_id'] for s in planned]
if len(ids)!=len(set(ids)):
    raise RuntimeError('Duplicate POIs in final route: correct selection constraints.')
selected_ids={p['id'] for pool in selected.values() for p in pool}
if set(ids)!=selected_ids:
    raise RuntimeError('Schedule lost a selected POI; this is a solver/scheduler bug.')
unsatisfied=[]
for wish in trip['wishes']:
    if not wish['mandatory']:continue
    matches=sum(1 for day in schedule for stop in day['activities']
        for p in city_pools[day['city']]
        if p['id']==stop['place_id'] and is_wish_match(p,wish,day['city']))
    if matches<max(1,wish['minimum']):unsatisfied.append(wish)
if unsatisfied:
    raise RuntimeError(f'Mandatory requests NOT satisfied: {unsatisfied}')


violations=[]
for day in schedule:
    city=day['city']
    for stop in day['activities']:
        place=next(p for p in city_pools[city] if p['id']==stop['place_id'])
        if is_prohibited(place,city):
            violations.append(f'Forbidden stop: {stop["name"]}')
        windows,source=allowed_windows(place,day['date'],city)
        if not any(a<=stop['start_min'] and stop['end_min']<=b for a,b in windows):
            violations.append(f'Outside opening window: {day["date"]} {stop["name"]}')
        rule=official_rule(place)
        if rule and rule.get('latest_start') is not None and stop['start_min']>rule['latest_start']:
            violations.append(f'After last admission: {stop["name"]}')
        if rule and rule.get('fixed_start') is not None and stop['start_min']!=rule['fixed_start']:
            violations.append(f'Incorrect departure: {stop["name"]}')
if violations:raise RuntimeError('Final strict validation failed: '+repr(violations))

# A statistical reporting section for the IRS evaluation; do not confuse
# endogenous utility scores with survey-rated human satisfaction.
by_id={p['id']:p for pool in city_pools.values() for p in pool}
utilities={uid:sum(by_id[s['place_id']]['fit'][uid] for s in planned) for uid in profile_ids}
served_groups=Counter(s['category'] for s in planned)
# A transparent preference-only baseline: take the highest-fit KG places, with
# identical stop counts, but deliberately without scheduling/route checks.
# This is a selection baseline, NOT a feasible itinerary comparator.
baseline=[];baseline_used=set()
for d,day in enumerate(schedule):
    candidates=[p for p in by_day[d] if p['id'] not in baseline_used]
    candidates.sort(key=lambda p:-(sum(p['fit'].values())+p.get('quality',0)))
    for p in candidates[:len(day['activities'])]:
        baseline.append(p);baseline_used.add(p['id'])
baseline_categories=Counter(p['group'] for p in baseline)
baseline_metrics={
    'name':'preference_only_top_K_no_schedule_validation',
    'comparison_warning':'NOT a time-feasible itinerary; measures selection bias only',
    'stops':len(baseline),'category_mix':dict(baseline_categories),
    'per_user_utility':{uid:sum(p['fit'][uid] for p in baseline) for uid in profile_ids}
}
# A poor-quality day must be visible in evaluation even when the rest of the
# optimized itinerary is feasible. This records rather than conceals gaps.
def day_fill_status(day,ix):
    path=selected[ix]
    if day['transfer']:return 'transfer'
    if any(p['group']=='major' or
           ('hiking' in p['tags'] and p['duration']>=180) or
           (p['group']=='outdoors' and p['duration']>=240) for p in path):
        return 'full_day_anchor'
    n=sum(p['group'] not in ('meal','shopping','evening') for p in path)
    return 'restaurant_or_evening_only' if n==0 else 'one_daytime_experience' if n==1 else 'well_composed'

daily_fill_audit=[]
for ix,day in enumerate(schedule):
    category=day_fill_status(day,ix)
    choices=day_options[ix]
    well_composed_alternatives=sum(
        sum(p['group'] not in ('meal','shopping','evening') for p in opt['path'])>=2
        for opt in choices)
    daily_fill_audit.append({
        'date':day['date'],'city':day['city'],'status':category,
        'selected_stops':[s['name'] for s in day['activities']],
        'feasible_2plus_daytime_alternatives_before_global_dedup':well_composed_alternatives})

metrics={
    'run_id':RUN_ID,'solver_status':solver_status,
    'group_size':len(profile_ids),'days':len(schedule),'scheduled_stops':len(planned),
    'category_mix':dict(served_groups),
    'preference_only_baseline':baseline_metrics,
    'per_user_model_utility':utilities,
    'users_without_reliable_preference_matches':[u for u in profile_ids if u not in fair_users],
    'preference_capacity':preference_capacity,
    'normalized_model_utility':{u:round(utilities[u]/preference_capacity[u],3) if preference_capacity[u] else None for u in profile_ids},
    'optimization_feasibility_cuts':0,
    'day_options_available':{day['date']:len(day_options[d]) for d,day in enumerate(days)},
    'modeled_route_minutes':sum(day['travel_minutes'] for day in schedule),
    'hours_basis':dict(Counter(s['hours_basis'] for s in planned)),
    'weather_basis':dict(Counter(day['weather']['source'] for day in schedule)),
    'google_failure_count':len(google_errors),
    'osm_error_count':len(osm_lookup_errors),
    'geoapify':dict(geoapify_counts),
    'geoapify_failures':geoapify_errors[:8],
    'assumed_opening_hours_stops':sum(s['hours_basis']=='assumed' for s in planned),
    'full_days_with_one_stop':sum(len(d['activities'])==1 and not d['transfer']
                                  for d in schedule),
    'ordinary_days_with_only_meal_or_evening':sum(x['status']=='restaurant_or_evening_only'
                                                  for x in daily_fill_audit),
    'ordinary_days_with_one_daytime_experience':sum(x['status']=='one_daytime_experience'
                                                    for x in daily_fill_audit),
    'day_fill_audit':daily_fill_audit,
    'day_fill_priority':{'zero_daytime_penalty':MEAL_ONLY_FULL_DAY_PENALTY,
                         'one_daytime_penalty':ONE_PRIMARY_FULL_DAY_PENALTY},
    'stops_per_day':{d['date']:len(d['activities']) for d in schedule},
    'osm_matched_hours':0,
    'trip_experience_groups':sorted({s['category'] for s in planned}),
    'self_guided_segments':sum(s['segment_type']=='self_guided' for d in schedule for s in d.get('segments',[])),
    'scheduled_self_guided_stops':sum(s.get('data_source')=='curated_self_guided' for s in planned),
    'explicit_prohibitions':trip.get('prohibited_themes',[]),
    'prohibited_named_places':trip.get('prohibited_places',[]),
    'final_prohibition_violations':0,
    'official_hours_stops':sum(s['hours_basis'].startswith('official') for s in planned),
    'supplemental_places_selected':[p['name'] for p in by_id.values()
                                    if p['id'] in selected_ids and p.get('data_source')!='Neo4j'],
    'repair_notes':[], 'unmet_mandatory_requests':unavailable_mandatory_wishes,
    'assumptions':['ORS static travel times (no live traffic)',
      'city centre is a temporary base where no hotel was supplied',
      'Geoapify community-sourced typical opening hours are not confirmed holiday/future hours',
      'intercity journeys are reserved as time buffers, not verified timetables'],
    'weights':W,
    'rating_model':'unused (no Google ratings)',
    'wish_coverage':[{'theme':w['theme'],'named_place':w.get('named_place'),
       'mandatory':w['mandatory'],'desired_count':w.get('desired_count',1),
       'scheduled_count':sum(is_wish_match(by_id[s['place_id']],w,d['city'])
           for d in schedule for s in d['activities'])} for w in trip['wishes']],
}

# Keep a transparent comparison of LA theme-park candidates that reached routing.
if 'Los Angeles' in city_pools and LA_THEME_PARK_REQUEST:
    hub='BASE:Los Angeles'
    metrics['la_theme_park_comparison']=[
        {'place':p['name'],'source':p.get('data_source','Neo4j'),
         'travel_from_base_min':routes.get((hub,p['id'])),
         'group_fit_total':sum(p['fit'].values()),
         'scheduled':p['id'] in selected_ids} for p in city_pools['Los Angeles']
        if p['group']=='major']

# Persist KG-derived place names and itinerary times, not bulk Google API data.
def save_json(filename,payload):
    (OUTPUT_DIR/filename).write_text(json.dumps(payload,indent=2,ensure_ascii=False,default=str),encoding='utf-8')

save_json('KPD_OFFICIAL_HOURS_REFERENCE.json',[
    {'venue':name,'official_source':rule['url'],
     'weekly_openings':rule['weekly'],
     'note':'published typical schedule; exceptions or dated tickets can change'}
    for name,rule in OFFICIAL_HOURS.items()])
save_json('KPD_PLAN.json',schedule)
save_json('KPD_METRICS.json',metrics)
save_json('KPD_DAY_FILL_AUDIT.json',daily_fill_audit)
save_json('KPD_TRIP_SLOTS.json',trip)
save_json('KPD_CANDIDATE_AUDIT.json',{
    city:[{'id':p['id'],'name':p['name'],'group':p['group'],
           'self_guided':bool(p.get('self_guided')),
           'official_hours_source':(official_rule(p) or {}).get('url'),
           'fit':p['fit'],'hours':bool(p.get('geoapify_hours')),
           'data_source':p.get('data_source','Neo4j'),
           'iconic':p['iconic']} for p in pool]
    for city,pool in city_pools.items()})
print('Category balance:',dict(served_groups))
print('Per-user modeled utility:',utilities)
print('Hours:',metrics['hours_basis'],'Weather:',metrics['weather_basis'])
if issues:print('Repairs:',issues)


if IS_SHARED_TRIP:
    public_preferences=[{'signup_interests':PROFILE_INTERESTS.get(uid, [])}
                        for uid in profile_ids]
else:
    public_preferences={
        uid:{'persona':users[uid]['persona_ids'],
             'preferred_activities':list(sorted(users[uid]['activities'],
                                          key=users[uid]['activities'].get,reverse=True))[:5],
             'signup_interests':PROFILE_INTERESTS.get(uid, [])}
        for uid in profile_ids
    }
compact_plan=[]
for d in schedule:
    compact_plan.append({
        'date':d['date'],'city':d['city'],'transfer_day':d['transfer'],
        'weather':d['weather'],
        'self_guided_segments':[{'title':s['title'],
                                  'start':f"{s['start_min']//60:02d}:{s['start_min']%60:02d}",
                                  'end':f"{s['end_min']//60:02d}:{s['end_min']%60:02d}",
                                  'anchored_to':s['anchor']} for s in d.get('segments',[])
                                if s['segment_type']=='self_guided'],
        'stops':[{'name':s['name'],'start':f"{s['start_min']//60:02d}:{s['start_min']%60:02d}",
                  'end':f"{s['end_min']//60:02d}:{s['end_min']%60:02d}",
                  'experience':s['category'],'themes':s['themes'],
                  'minutes_from_previous':s['route_in_minutes'],
                  'place_id':s['place_id'],'suitable_for':s['suitable_for'],
                  'hours_basis':s['hours_basis']} for s in d['activities']]
    })

TRAVELER_CONTEXT_LABEL = ('Traveler signup interests:' if IS_SHARED_TRIP else
                          'Who is traveling (persona-based tastes; these are NOT personal names):')
FINAL_PROMPT=f'''You are an excellent human travel editor, writing a California itinerary for friends.
The complete choices, sequence and dates passed the integrated optimizer and route checks.
Write an enjoyable, useful trip guide with the judgment of a thoughtful local planner.

Original travel request:
{USER_PROMPT}

{TRAVELER_CONTEXT_LABEL}
{json.dumps(public_preferences,ensure_ascii=False)}

Selected real places and their intended schedule:
{json.dumps(compact_plan,ensure_ascii=False)}

Writing requirements:
- Reflect group fit and diversity over the FULL TRIP; a particular day can
  cater more to one traveler. Don't claim equal delight every day.
- When the user REQUESTS theme parks, Anaheim is a legitimate LA day trip;
  choose the best eligible option without automatically picking Disneyland.
  If the user prohibits theme parks, never mention one as a planned suggestion.
- Explain what is exciting or distinctive about each place, but don't invent events,
  menus, special exhibitions, bookings, entry privileges or venue schedules.
- Preserve all selected places and scheduled order, including the self-guided
  segments without moving other scheduled stops. Never add an extra place as
  if it were a booked or verified part of the optimized itinerary.
- Treat a long theme-park visit as a full experience; don't apologetically call
  a short day 'intentionally relaxed' unless that is genuinely appropriate.
- A meal is a meal; don't market every restaurant as the highlight of the day.
- Treat scheduled 'curated_self_guided' stops (beaches, public neighborhoods, trails)
  as genuine planned activities with exact start/end times, not expendable
  suggestions or fictitious ticketed attractions. Their route and time slot
  were evaluated like every other candidate.
- Explicit prohibitions in the request are absolute: never reintroduce excluded
  categories or forbidden named places in any scheduled or suggested content.
- Include the provided self-guided segments as proper daytime itinerary blocks
  (beach time, walking, browsing or exploration), with the allocated times.
  These are unbooked self-directed activities at the named KG-backed anchor,
  NOT another attraction, invented venue or confirmed guided tour.
- Don't label every walk 'optional/unverified'. It is part of the planned day,
  though no booking is claimed. Never invent a named extra destination.
- Never disguise a sparse schedule with fabricated activities. If a day is naturally
  lighter, describe free time as unstructured; do not add unsourced named places.
- Give natural transitions and a few useful neighborhood ideas without adding
  named unsourced locations. Be conversational, not robotic or algorithmic.
- No numerical optimization metrics, traveler IDs in explanations, fake confidence,
  or repeated 'check opening hours' warnings at every stop.
- For weather: when source is forecast, describe a tentative forecast. When source
  is seasonal_history_NOT_forecast, call it a historical seasonal expectation,
  NEVER a forecast. If unknown, don't mention weather for that day.
- Geoapify times come from community-contributed typical OSM schedules, not guaranteed future service.
- Assumed hours are hypothetical planning windows, NOT reliable opening times.
- Do not imply a venue has a performance merely because it's a theatre or concert hall.
- End with ONE concise practical note mentioning future hours, tickets, hotel-base
  approximations and unverified intercity transit. Don't claim reservations exist.
- Never claim assumed venue times are verified or a fixed-time service operates on an unconfirmed future date.
- Avoid 'Google says', 'algorithm selected', 'this balances utility', etc.
- Include concrete day-by-day plans at useful times and an engaging short intro.
'''
# (OUTPUT_DIR/'KPD_FINAL_PROMPT.txt').write_text(FINAL_PROMPT,encoding='utf-8')
print('\n========== FINAL OPENAI PROMPT ==========\n')
print(FINAL_PROMPT)
# Increased the request timeout to 300 seconds to prevent ReadTimeout errors on long itinerary generation
answer=client.responses.create(model=ITINERARY_MODEL,input=FINAL_PROMPT,max_output_tokens=10000,timeout=300.0)
ITINERARY=answer.output_text
# (OUTPUT_DIR/'KPD_ITINERARY.md').write_text(ITINERARY,encoding='utf-8')
display(Markdown(ITINERARY))
import shutil
archive=shutil.make_archive(f'KPD_{RUN_ID}','zip',OUTPUT_DIR)
print('Done. Outputs:',OUTPUT_DIR.resolve(),'ZIP:',archive)
driver.close()