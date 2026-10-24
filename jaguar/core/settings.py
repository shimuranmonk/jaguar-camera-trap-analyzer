import json
import sqlite3
import sys
import os
from contextlib import closing
from pathlib import Path
from jaguar.ingest.service import import_lock


def defaults():
    resources=Path(os.environ['JAGUAR_RESOURCE_ROOT']) if os.environ.get('JAGUAR_RESOURCE_ROOT') else (Path(sys.executable).parent/'resources' if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]/'assets')
    return {'detection_threshold':.2,'timestamp_tolerance_seconds':0,'temperature_tolerance_c':1,
        'device':'auto','exiftool_path':str(resources/'exiftool/ExifTool.exe'),
        'ocr_models':str(resources/'ocr-models'),'detector_models':str(resources/'detector-models'),
        'species_models':str(resources/'species-models'),'species_python':str(resources/'species-runtime/python.exe'),
        'species_country':'','species_admin1':''}


def read_settings(database):
    with closing(sqlite3.connect(database)) as connection:
        stored=json.loads(connection.execute('SELECT settings_json FROM projects').fetchone()[0])
    return dict(defaults(),**stored)


def save_settings(database,settings):
    country=settings.get('species_country','').strip().upper()
    admin=settings.get('species_admin1','').strip().upper()
    if country and (len(country)!=3 or not country.isascii() or not country.isalpha()):
        raise ValueError('The optional species country must be a three-letter ISO code.')
    if admin and (country!='USA' or len(admin)!=2 or not admin.isascii() or not admin.isalpha()):
        raise ValueError('Species state filtering currently requires USA and a two-letter state code.')
    settings=dict(settings,species_country=country,species_admin1=admin)
    if not 0<=float(settings['detection_threshold'])<=1:
        raise ValueError('Detection threshold is outside zero to one.')
    if float(settings['timestamp_tolerance_seconds'])<0 or float(settings['temperature_tolerance_c'])<0:
        raise ValueError('Reconciliation tolerances cannot be negative.')
    if settings['device'] not in {'auto','cpu'}:
        raise ValueError('Unsupported processing device.')
    with import_lock(database),closing(sqlite3.connect(database)) as connection:
        with connection:
            connection.execute('UPDATE projects SET settings_json=?',(json.dumps(settings),))
