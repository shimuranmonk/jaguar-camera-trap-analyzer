import json
import sqlite3
from contextlib import closing
from uuid import uuid4
from jaguar.db.projects import open_project
from jaguar.ingest.service import import_lock, now


def assign_species_location(database,image_id,country,actor,reason):
    country=country.strip().upper()
    if country and (len(country)!=3 or not country.isascii() or not country.isalpha()):
        raise ValueError('The country must be a three-letter ISO code or blank for project default.')
    if not actor.strip() or not reason.strip():
        raise ValueError('A reviewer and reason are required.')
    with import_lock(database):
        open_project(database)
        with closing(sqlite3.connect(database)) as c:
            c.execute('PRAGMA foreign_keys=ON')
            old=c.execute('SELECT country FROM species_locations WHERE image_id=?',(image_id,)).fetchone()
            with c:
                c.execute('INSERT OR REPLACE INTO species_locations VALUES (?,?,?,?,?,?)',(image_id,country,'',actor,reason,now()))
                c.execute('INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?,?)',(str(uuid4()),image_id,'species_location',json.dumps(old),json.dumps(country),actor,reason,'location_assignment',now()))


def label_fields(label):
    parts = (label or '').split(';')
    if len(parts) != 7:
        return {'label': label, 'taxon_id': None, 'class': None, 'order': None, 'family': None,
                'genus': None, 'species': None, 'scientific_name': None, 'common_name': label}
    identity, class_, order, family, genus, species, common = parts
    return dict(label=label, taxon_id=identity, **{'class': class_, 'order': order}, family=family,
        genus=genus, species=species, scientific_name=' '.join(v for v in [genus, species] if v) or None, common_name=common)


def review_species(database, image_id, target_key, label, scientific_name, actor, reason,
                   status='confirmed', detection_id=None, source_record_id=None):
    if not actor.strip() or not reason.strip():
        raise ValueError('A reviewer and reason are required.')
    if status not in {'confirmed', 'unidentified', 'unreviewed'}:
        raise ValueError('Unsupported identification review status.')
    if status == 'confirmed' and not (label or '').strip():
        raise ValueError('A confirmed label is required.')
    with import_lock(database):
        open_project(database)
        with closing(sqlite3.connect(database)) as c:
            c.execute('PRAGMA foreign_keys=ON')
            if detection_id and not c.execute('SELECT 1 FROM detections WHERE id=? AND image_id=?',(detection_id,image_id)).fetchone():
                raise ValueError('The detection belongs to another image.')
            if source_record_id and not c.execute("SELECT 1 FROM processing_records r JOIN processing_runs p ON p.id=r.run_id WHERE r.id=? AND r.image_id=? AND p.stage='species'",(source_record_id,image_id)).fetchone():
                raise ValueError('The species prediction belongs to another image.')
            old=c.execute('SELECT label,scientific_name,status FROM species_reviews WHERE image_id=? AND target_key=? ORDER BY rowid DESC LIMIT 1',(image_id,target_key)).fetchone()
            identity,at=str(uuid4()),now()
            value=[label,scientific_name,status]
            with c:
                c.execute('INSERT INTO species_reviews VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (identity,image_id,target_key,detection_id,source_record_id,label,scientific_name,status,actor.strip(),reason.strip(),at))
                c.execute('INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?,?)',
                    (str(uuid4()),image_id,'species:'+target_key,json.dumps(old),json.dumps(value),actor.strip(),reason.strip(),'species_review',at))
            return identity
