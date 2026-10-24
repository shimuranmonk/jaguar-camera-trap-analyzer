import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import threading


class SpeciesFailure(ValueError):
    def __init__(self,details):
        self.details=details
        super().__init__(details['error'])


class SpeciesEngine:
    def __init__(self, python, model_root, country='', admin1=''):
        self.root = Path(model_root).resolve()
        if not Path(python).is_file() or not (self.root / 'info.json').is_file():
            raise ValueError('A local SpeciesNet interpreter and model folder are required.')
        self.country, self.admin1 = country, admin1
        info = json.loads((self.root / 'info.json').read_text(encoding='utf-8'))
        self.assets = {'manifest':hashlib.sha256((self.root/'info.json').read_bytes()).hexdigest()}
        for key in ['classifier', 'classifier_labels', 'taxonomy', 'geofence', 'detector']:
            path = self.root / info[key]
            if not path.is_file():
                raise ValueError('Missing local SpeciesNet asset: ' + key)
            with path.open('rb') as handle:
                self.assets[key] = hashlib.file_digest(handle, 'sha256').hexdigest()
        self.messages = queue.Queue()
        self.process = subprocess.Popen([str(python), '-u', str(Path(__file__).resolve().parents[1] / 'workers/species_runtime.py')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
            errors='replace',env=dict(os.environ,PYTHONIOENCODING='utf-8',PYTHONUTF8='1'),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        def receive():
            try:
                for line in self.process.stdout:
                    if line.startswith('JAGUAR_RESULT:'):
                        self.messages.put(json.loads(line[len('JAGUAR_RESULT:'):]))
            finally:
                self.messages.put({'error': 'The SpeciesNet interpreter stopped.'})
        threading.Thread(target=receive, daemon=True).start()
        try:
            ready = self.request({'model_root': str(self.root)})
            self.runtime_info=ready
            self.version = 'JAGUAR Species adapter 2 | SpeciesNet ' + ready['package_version'] + ' | model ' + ready['model_version'] + ' | ' + json.dumps(self.assets, sort_keys=True)
        except Exception:
            self.close()
            raise

    def request(self, value):
        self.process.stdin.write(json.dumps(value) + '\n')
        self.process.stdin.flush()
        try:
            result = self.messages.get(timeout=600)
        except queue.Empty:
            self.close()
            raise ValueError('SpeciesNet exceeded its processing timeout.')
        if result.get('error'):
            raise SpeciesFailure(result)
        return result

    def extract_for_source(self, connection, image_id, path):
        record = connection.execute("SELECT r.id,r.status FROM processing_records r JOIN processing_runs p ON p.id=r.run_id WHERE r.image_id=? AND p.stage='detect' ORDER BY r.rowid DESC LIMIT 1", (image_id,)).fetchone()
        if not record or record[1] != 'succeeded':
            raise ValueError('A successful latest detection run is required; manual species annotation remains available.')
        targets = [{'detection_id': d[0], 'class': d[1], 'confidence': d[2], 'box': list(d[3:])} for d in connection.execute(
            'SELECT id,class,confidence,x,y,width,height FROM detections WHERE record_id=? ORDER BY rowid', (record[0],))]
        geolocation = {k: v for k, v in [('country', self.country), ('admin1_region', self.admin1)] if v}
        override=connection.execute('SELECT country,admin1 FROM species_locations WHERE image_id=?',(image_id,)).fetchone()
        if override and override[0]:
            geolocation=dict(country=override[0])
            if override[1]:
                geolocation['admin1_region']=override[1]
        native = self.request({'path': str(path), 'targets': targets, 'geolocation': geolocation}) if targets else {'native': {'filepath': str(path), 'detections': []}}
        return dict(native, targets=targets, detector_record_id=record[0], engine_version=self.version,
            runtime_info=getattr(self,'runtime_info',{}),
            assets_sha256=self.assets, device='cpu', geolocation=geolocation,
            outcome='suggestions_require_review' if targets else 'no_accepted_detection_requires_review')

    def close(self):
        if getattr(self, 'process', None):
            try:
                self.process.stdin.close()
                self.process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait()
            self.process.stdout.close()
