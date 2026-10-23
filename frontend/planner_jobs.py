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


def start_job(prompt, profile_ids, owner_email):
    job_id = uuid.uuid4().hex[:16]
    folder = DATA / job_id
    folder.mkdir(parents=True)
    env = os.environ.copy()
    env.update(KPD_TRIP_PROMPT=prompt, KPD_PROFILE_IDS=json.dumps(profile_ids),
               KPD_OWNER_PROFILE_ID=profile_ids[0], KPD_OUTPUT_ROOT=str(folder),
               PYTHONUNBUFFERED='1')
    record = {'job_id': job_id, 'status': 'running', 'profile_ids': profile_ids,
              'prompt': prompt, 'folder': str(folder), 'owner_email':owner_email}
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
    answer = {'job_id': job_id, 'status': data['status']}
    if data['status'] == 'completed':
        directory = Path(data['result_dir'])
        for filename, field in [('KPD_PLAN.json','plan'), ('KPD_TRIP_SLOTS.json','slots'),
                                 ('KPD_METRICS.json','metrics'), ('KPD_ITINERARY.md','itinerary')]:
            file = directory / filename
            if file.exists():
                answer[field] = json.loads(file.read_text()) if filename.endswith('.json') else file.read_text()
    elif data['status'] == 'failed':
        log = Path(data['folder'])/'run.log'
        answer['error'] = data.get('error') or (log.read_text(errors='replace')[-1800:] if log.exists() else 'Planner failed')
    return answer
