import json
from jaguar.core.species import label_fields


def latest_reviews(connection,image):
    return connection.execute('SELECT s.* FROM species_reviews s WHERE image_id=? AND NOT EXISTS (SELECT 1 FROM species_reviews newer WHERE newer.image_id=s.image_id AND newer.target_key=s.target_key AND newer.rowid>s.rowid) ORDER BY target_key',(image,)).fetchall()


def summary(connection,image):
    result={'species_status':'not_processed','species_suggestions':None,'species_confirmed_labels':None,
            'species_confirmed_scientific_names':None,'species_confirmed_count':0,'species_review_required':True,
            'species_filter_country':None,'species_filter_admin1':None}
    row=connection.execute("SELECT r.id,r.status,r.payload_json FROM processing_records r JOIN processing_runs p ON p.id=r.run_id WHERE image_id=? AND p.stage='species' ORDER BY r.rowid DESC LIMIT 1",(image,)).fetchone()
    targets=[]
    if row:
        result.update(species_record_id=row[0],species_status=row[1])
        data=json.loads(row[2]) if row[2] else {}
        result['species_filter_country']=data.get('geolocation',{}).get('country')
        result['species_filter_admin1']=data.get('geolocation',{}).get('admin1_region')
        targets=[t['detection_id'] for t in data.get('targets',[])]
        suggestions=[label_fields(d.get('processed_classification',{}).get('class'))['common_name'] for d in data.get('native',{}).get('detections',[])]
        result['species_suggestions']='; '.join(v for v in suggestions if v) or None
    reviews=latest_reviews(connection,image)
    confirmed=[r for r in reviews if r[7]=='confirmed']
    result['species_confirmed_labels']='; '.join(r[5] for r in confirmed) or None
    result['species_confirmed_scientific_names']='; '.join(r[6] for r in confirmed if r[6]) or None
    result['species_confirmed_count']=len(confirmed)
    reviewed={r[2] for r in reviews if r[7] in {'confirmed','unidentified'}}
    result['species_review_required']=not bool(targets or confirmed) or bool(set(targets)-reviewed) or any(r[7]=='unidentified' for r in reviews)
    return result


def write_species(connection,sheets,chunks):
    def records():
        # Sort compact identities, never the complete score-vector payloads.
        for identity, in connection.execute("SELECT r.id FROM processing_records r JOIN processing_runs p ON p.id=r.run_id JOIN export_scope s ON s.id=r.image_id WHERE p.stage='species' ORDER BY r.image_id,r.rowid"):
            yield connection.execute('SELECT id,run_id,image_id,status,payload_json,error FROM processing_records WHERE id=?',(identity,)).fetchone()
    def raw_rows():
        for record,run,image,status,payload,error in records():
            if payload:
                for part,text in chunks(payload):
                    yield record,run,image,status,part,text,error
            else:
                yield record,run,image,status,1,None,error
    sheets.write('Species Raw',['record_id','run_id','image_id','status','part','payload_json','error'],raw_rows(),
        'One row per complete SpeciesNet payload chunk; all historical runs, unknown output fields and errors retained')
    def identified():
        for record,run,image,status,payload,error in records():
            data=json.loads(payload) if payload else {}
            for target,det in zip(data.get('targets',[]),data.get('native',{}).get('detections',[])):
                processed=det.get('processed_classification',{})
                fields=label_fields(processed.get('class'))
                yield (record,run,image,target['detection_id'],*target['box'],target['class'],target['confidence'],
                    fields['label'],fields['taxon_id'],fields['common_name'],fields['scientific_name'],
                    fields['class'],fields['order'],fields['family'],fields['genus'],fields['species'],
                    processed.get('score'),processed.get('source'),data.get('native',{}).get('model_version'),data.get('device'),
                    data.get('geolocation',{}).get('country'),data.get('geolocation',{}).get('admin1_region'),'unreviewed_model_suggestion')
    sheets.write('Species Identifications',['record_id','run_id','image_id','detection_id','x','y','width','height',
        'detector_class','detector_confidence','raw_label','taxon_id','common_name','scientific_name','taxon_class','taxon_order',
        'family','genus','species','prediction_score','prediction_source','model_version','device','filter_country','filter_admin1','label_status'],identified(),
        'One row per historical image/detection suggestion; scores are model scores, not guarantees; confirmations remain separate')
    def candidates():
        for record,run,image,status,payload,error in records():
            data=json.loads(payload) if payload else {}
            for target,det in zip(data.get('targets',[]),data.get('native',{}).get('detections',[])):
                raw=det.get('raw_classifications',{})
                for rank,(label,score) in enumerate(zip(raw.get('classes',[]),raw.get('scores',[])),1):
                    fields=label_fields(label)
                    yield record,run,image,target['detection_id'],rank,label,fields['taxon_id'],fields['common_name'],fields['scientific_name'],score
    sheets.write('Species Candidates',['record_id','run_id','image_id','detection_id','rank','raw_label','taxon_id','common_name','scientific_name','score'],candidates(),
        'One row per returned candidate; complete classifier score vectors retained, including non-species labels')
    sheets.write('Species Review History',['review_id','image_id','target_key','detection_id','source_record_id','confirmed_label',
        'scientific_name','status','reviewer','reason','reviewed_at'],connection.execute('SELECT r.* FROM species_reviews r JOIN export_scope s ON s.id=r.image_id ORDER BY image_id,r.rowid'),
        'One row per immutable human species-review action; latest row per image/target defines current state')
