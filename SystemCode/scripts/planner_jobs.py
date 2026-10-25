"""Start and read itinerary generation jobs."""
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / 'planner_results'
DATA.mkdir(exist_ok=True)
_LOCK = threading.Lock()
_JOBS = {}


def start_job(prompt, profile_ids, owner_email, profile_interests=None, shared_trip_id=None, shared_base_prompt=None):
    job_id = uuid.uuid4().hex[:16]
    folder = DATA / job_id
    folder.mkdir(parents=True)
    env = os.environ.copy()
    env.update(KPD_TRIP_PROMPT=prompt, KPD_PROFILE_IDS=json.dumps(profile_ids),
               KPD_PROFILE_INTERESTS=json.dumps(profile_interests or {}),
               KPD_OWNER_PROFILE_ID=profile_ids[0], KPD_OUTPUT_ROOT=str(folder),
               KPD_IS_SHARED_TRIP='true' if shared_trip_id else 'false',
               PYTHONUNBUFFERED='1')
    record = {'job_id': job_id, 'status': 'running', 'profile_ids': profile_ids,
              'prompt': prompt, 'folder': str(folder), 'owner_email':owner_email,
              'shared_trip_id': shared_trip_id, 'shared_base_prompt': shared_base_prompt,
              'shared_published': False}
    with _LOCK:
        _JOBS[job_id] = record
    def execute():
        log_path = folder / 'run.log'
        try:
            with log_path.open('w', encoding='utf-8') as log:
                process = subprocess.run([sys.executable, str(HERE / 'planner_engine.py')],
                        cwd=str(HERE.parent), env=env, stdout=log, stderr=subprocess.STDOUT,
                        timeout=1200, check=False)
            results = list(folder.glob('*/KPD_PLAN.json'))
            if not results:
                # Support a future change to the plan file name.
                results = list(folder.glob('*/KPD_TRIP_SLOTS.json'))
            record['status'] = 'completed' if process.returncode == 0 and results else 'failed'
            record['returncode'] = process.returncode
            if results:
                record['result_dir'] = str(results[0].parent)
        except Exception as exc:
            record.update(status='failed', error=str(exc))
    threading.Thread(target=execute, daemon=True).start()
    return job_id


def get_job(job_id, owner_email):
    with _LOCK:
        data = dict(_JOBS.get(job_id) or {})
    if not data or data.get('owner_email') != owner_email:
        return None
    answer = {'job_id': job_id, 'status': data['status'], 'sharedTripId': data.get('shared_trip_id')}
    if data['status'] == 'completed':
        directory = Path(data['result_dir'])
        for filename, field in [('KPD_PLAN.json','plan'), ('KPD_PLAN.json','slots'),
                                 ('KPD_METRICS.json','metrics'), ('KPD_ITINERARY.md','itinerary')]:
            file = directory / filename
            if file.exists():
                answer[field] = json.loads(file.read_text()) if filename.endswith('.json') else file.read_text()
    elif data['status'] == 'failed':
        # Keep planner diagnostics in run.log on the server. They are verbose,
        # may include internal data, and are not useful as a browser status.
        answer['error'] = 'The itinerary could not be generated. Some requested activities may not be available for these dates and destination.'
    return answer


def claim_shared_publication(job_id, owner_email):
    with _LOCK:
        record = _JOBS.get(job_id)
        if (not record or record.get('owner_email') != owner_email or
                record.get('status') != 'completed' or not record.get('shared_trip_id') or
                record.get('shared_published')):
            return None
        record['shared_published'] = True
        return dict(record)