import sqlite3
from contextlib import closing
from uuid import uuid4
from jaguar.ingest.service import import_lock, now
from jaguar.core.reconciliation import canonical


def add_camera(database,label,make=None,model=None,serial=None):
    if not label.strip():
        raise ValueError('A physical camera label is required.')
    identity=str(uuid4())
    with import_lock(database),closing(sqlite3.connect(database)) as connection:
        with connection:
            connection.execute('INSERT INTO cameras VALUES (?,?,?,?,?,?)',(identity,make or None,model or None,serial or None,label.strip(),now()))
    return identity


def add_deployment(database,camera_id,site_label,start=None,end=None,notes=''):
    from datetime import datetime
    if not site_label.strip():
        raise ValueError('A site label is required.')
    if start:
        datetime.fromisoformat(start)
    if end:
        datetime.fromisoformat(end)
    if start and end and start>end:
        raise ValueError('Deployment end must follow its start.')
    identity=str(uuid4())
    with import_lock(database),closing(sqlite3.connect(database)) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        with connection:
            connection.execute('INSERT INTO deployments VALUES (?,?,?,?,?,?)',(identity,camera_id,site_label.strip(),start,end,notes))
    return identity


def assign_camera(database,image_id,camera_id,deployment_id,actor='local operator'):
    with import_lock(database),closing(sqlite3.connect(database)) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        if deployment_id:
            deployment=connection.execute('SELECT camera_id FROM deployments WHERE id=?',(deployment_id,)).fetchone()
            if deployment is None or deployment[0]!=camera_id:
                raise ValueError('The deployment must belong to the selected physical camera.')
        old=connection.execute('SELECT camera_id,deployment_id FROM image_assignments WHERE image_id=? ORDER BY rowid DESC LIMIT 1',(image_id,)).fetchone()
        with connection:
            connection.execute('INSERT INTO image_assignments VALUES (?,?,?,?,?,?)',(str(uuid4()),image_id,camera_id,deployment_id,actor,now()))
            connection.execute('INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?,?)',(str(uuid4()),image_id,'camera_assignment',canonical(old),canonical([camera_id,deployment_id]),actor,'Manual physical camera/deployment assignment.','assignment',now()))
