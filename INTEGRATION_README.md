# K Perfect Days — integrated website + itinerary optimizer

## What is integrated

- Existing login, signup, shared-trip invitations, Neo4j user/persona relationships, Discover results and group voting are retained.
- The original 11 planning stages from notebook v7.5.2 are extracted to `frontend/planner_engine.py`. Its solver, KG queries, weather, Geoapify enrichment, ORS routes and final GPT writing remain in place.
- `frontend/server.py` exposes `POST /api/itineraries` and `GET /api/itineraries/<job_id>` to authenticated users.
- `frontend/planner_jobs.py` runs the planner in a separate process and stores each run privately under `frontend/planner_results/<job_id>/`.
- `frontend/index.html`, `app.js` and `styles.css` add a natural-language planner and day-by-day view on the Plan a trip page.
- The real logged-in Neo4j profile becomes the planner owner; members of an existing shared trip are included from authenticated account records. Profile IDs typed in a prompt do not override membership.

## Installation

Use Python 3.11 or 3.12. From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r frontend/requirements.txt
```

Create your own environment variables (do not commit real keys):

```bash
export NEO4J_URI='neo4j+s://YOUR_INSTANCE.databases.neo4j.io'
export NEO4J_USERNAME='neo4j'
export NEO4J_PASSWORD='YOUR_NEW_PASSWORD'
export OPENAI_API_KEY='YOUR_KEY'
export ORS_API_KEY='YOUR_KEY'
export GEOAPIFY_API_KEY='YOUR_KEY'
```

On Windows PowerShell use `$env:VARIABLE_NAME = 'value'` for each key. `GEOAPIFY_API_KEY` can be blank (the planner falls back to assumed hours). The integrated server uses the same Neo4j instance for user onboarding and itinerary planning.

Start the web server from the **repository root**:

```bash
python frontend/server.py
```

Open http://127.0.0.1:8000/frontend/. Create or log into a user account. Choose **Plan a trip**, type a complete plain-English request into **Plan your perfect days**, and press **Generate itinerary**. If you want a group result, create a shared-trip invitation, have friends join, then generate from the same trip. The request is handled asynchronously; the browser displays its result on completion.

Example request:

> Plan three nights in Los Angeles starting next Friday. We like museums, beaches and good food, but no theme parks. Keep travel reasonable.

## JSON API

`POST /api/itineraries` (Bearer session token):

```json
{"prompt":"Plan three nights in San Francisco starting next Friday, with museums, coastal walks and food.","sharedTripId":null}
```

Returns HTTP 202 and a `job_id`. `GET /api/itineraries/<job_id>` returns `{status:"running"}` or `{status:"completed", plan:[...], metrics:{...}, itinerary:"..."}`. Failure returns `{status:"failed", error:"..."}`. Jobs are scoped to the submitting account, kept in process memory, and disappear from the API after a server restart. Their run files remain in the private output folder.

## Exactly which files changed

| File | Change |
|---|---|
| `frontend/server.py` | Added authenticated itinerary create/read APIs, member resolution and restricted access to private output files. |
| `frontend/user_service.py` | Removed hardcoded Neo4j password and connection settings; uses environment variables. |
| `frontend/app.js` | Added submit/poll/render functions for planner results. Existing Discover and votes unchanged. |
| `frontend/index.html` | Added free-text planning panel. |
| `frontend/styles.css` | Added styling for the planner and result cards. |
| `frontend/.gitignore` | Ignore secrets, private users/trips and output directories. |
| `frontend/planner_engine.py` | New executable Python extraction of the notebook with browser-supplied inputs and safe runtime credentials. |
| `frontend/planner_jobs.py` | New lightweight background process manager. |
| `frontend/requirements.txt` | Dependencies for running the website and extracted planner. |
| `frontend/.env.example` | Names of required runtime configuration values (not a secret file). |

## Test status and limitations

- Passed: syntax checks for all four Python source files, JS syntax check, static integration checks, and offline authorization / job-output tests.
- Not verified here: execution against live Neo4j / OpenAI / Geoapify / ORS accounts or the full CP-SAT end-to-end itinerary. Test this with your credentials before you declare the application production-ready.
- This service is a course demonstration, not production hosting. Jobs have an in-memory registry and no persistent queue; running several 20+ night plans concurrently can be expensive. The parser still accepts California-only destinations and up to 30 nights, as in v7.5.2.
- The date/participants the planner uses come from the **natural-language request** and authenticated shared-trip membership respectively. The original date pickers still belong to the lightweight Discover flow. Include dates and destinations in the text box for the optimizer.
- No booking verification is promised: Geoapify or published weekly hours may be incomplete, and unknown opening hours are marked as assumptions.
- Rotate any credentials shared in notebooks or earlier repositories before a public GitHub push. The original root `users.json` demo file and `test.py` with potential private data were excluded from this integration ZIP; keep real account data local.

## Troubleshooting

- `KeyError: NEO4J_URI`: export environment variables in the same terminal before starting the server.
- Missing Python module: activate your virtual environment, rerun `pip install -r frontend/requirements.txt`.
- Planner job fails: the page displays the last relevant lines from `frontend/planner_results/<job_id>/run.log`; review the full private log locally.
- Missing profile: ensure you signed up through the website and its Neo4j user service created a `profileId` in the same graph the planner queries.
- Job takes too long: test a three-night, one-city prompt first; ORS, Geoapify, CP-SAT and the final LLM may take significant time on long itineraries.
