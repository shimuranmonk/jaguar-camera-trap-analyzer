import sqlite3
from contextlib import closing
from datetime import date


def dashboard_data(database,camera=None,start=None,end=None,run=None):
    for value in [start,end]:
        if value:
            date.fromisoformat(value)
    if start and end and start>end:
        raise ValueError('End date must follow start date.')
    conditions=['1=1']
    args={}
    if camera:
        conditions.append("(SELECT camera_id FROM image_assignments a WHERE a.image_id=i.id ORDER BY rowid DESC LIMIT 1) IS NULL" if camera=='unassigned' else "(SELECT camera_id FROM image_assignments a WHERE a.image_id=i.id ORDER BY rowid DESC LIMIT 1)=:camera")
        args['camera']=camera
    for value,name,comparison in [(start,'start','>='),(end,'end','<=')]:
        if value:
            conditions.append("(SELECT substr(json_extract(value_json,'$.iso8601'),1,10) FROM metadata_resolved WHERE image_id=i.id AND field='capture_timestamp')"+comparison+':'+name)
            args[name]=value
    if run:
        conditions.append('EXISTS(SELECT 1 FROM processing_records WHERE image_id=i.id AND run_id=:run)')
        args['run']=run
    scope='SELECT i.id FROM images i WHERE '+' AND '.join(conditions)
    common='''WITH scope AS ('''+scope+'''),
        latest_exif AS (SELECT e.* FROM exif_records e JOIN scope s ON s.id=e.image_id WHERE e.rowid=(SELECT max(rowid) FROM exif_records WHERE image_id=e.image_id)),
        latest_stage AS (SELECT r.*,p.stage FROM processing_records r JOIN processing_runs p ON p.id=r.run_id JOIN scope s ON s.id=r.image_id WHERE r.rowid=(SELECT max(q.rowid) FROM processing_records q JOIN processing_runs z ON z.id=q.run_id WHERE q.image_id=r.image_id AND z.stage=p.stage)),
        selected AS (SELECT m.* FROM metadata_resolved m JOIN scope s ON s.id=m.image_id)
    '''
    with closing(sqlite3.connect(database,timeout=.1)) as connection:
        def rows(query):
            return connection.execute(common+query,args).fetchall()
        def count(query):
            return rows(query)[0][0]
        total=count('SELECT count(*) FROM scope')
        paths=count('SELECT count(*) FROM image_paths p JOIN scope s ON s.id=p.image_id WHERE is_available=1')
        represented=count('SELECT count(DISTINCT image_id) FROM image_paths p JOIN scope s ON s.id=p.image_id WHERE is_available=1')
        completed=count("SELECT count(*) FROM scope s WHERE EXISTS(SELECT 1 FROM latest_exif e WHERE e.image_id=s.id AND e.status='succeeded') AND (SELECT count(DISTINCT stage) FROM latest_stage r WHERE r.image_id=s.id AND r.status='succeeded' AND stage IN ('ocr','detect','reconcile'))=3")
        categories=dict(rows("SELECT d.class,count(DISTINCT d.image_id) FROM detections d JOIN latest_stage r ON r.id=d.record_id WHERE r.stage='detect' AND r.status='succeeded' GROUP BY d.class"))
        missing=count("SELECT count(*) FROM latest_stage WHERE stage='detect' AND status='succeeded' AND json_extract(payload_json,'$.outcome')='no_accepted_detection'")
        reviews=dict(rows("SELECT status,count(*) FROM reviews r JOIN scope s ON s.id=r.image_id WHERE status<>'superseded' GROUP BY status"))
        review_images=count("SELECT count(DISTINCT image_id) FROM reviews r JOIN scope s ON s.id=r.image_id WHERE status='open'")
        failures=count("SELECT (SELECT count(*) FROM latest_exif WHERE status='failed')+(SELECT count(*) FROM latest_stage WHERE status='failed')")
        coverage={field:count("SELECT count(*) FROM selected WHERE field='"+field+"' AND value_json IS NOT NULL AND value_json<>'null'") for field in ['capture_timestamp','make','model','site_label','temperature_c','latitude']}
        timeline=rows("SELECT substr(json_extract(value_json,'$.iso8601'),1,10) AS day,count(*) FROM selected WHERE field='capture_timestamp' AND json_extract(value_json,'$.iso8601') IS NOT NULL GROUP BY day ORDER BY day DESC LIMIT 15")
        cameras=rows("SELECT coalesce(json_extract(m.value_json,'$'),'Unknown'),count(*) FROM scope s LEFT JOIN metadata_resolved m ON m.image_id=s.id AND m.field='make' GROUP BY 1 ORDER BY 2 DESC")
        return {'total':total,'paths':paths,'duplicates':paths-represented,'processed':completed,'categories':categories,'no_detection':missing,'reviews':reviews,'review_images':review_images,'failures':failures,'coverage':coverage,'timeline':timeline,'cameras':cameras}
