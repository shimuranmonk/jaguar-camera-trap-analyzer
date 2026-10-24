"""One-row-per-image source columns for researcher analysis."""
import json
from pathlib import Path


def analysis_rows(connection):
    def latest(sql, image):
        return connection.execute(sql, (image,)).fetchone()

    for image, in connection.execute('SELECT id FROM export_scope ORDER BY id'):
        path = latest('SELECT absolute_path FROM image_paths WHERE image_id=? ORDER BY is_available DESC,absolute_path LIMIT 1', image)
        row = {'image_id': image, 'filename': Path(path[0]).name if path else None}
        exif = latest('SELECT id,run_id,status,raw_json,normalized_json FROM exif_records WHERE image_id=? ORDER BY rowid DESC LIMIT 1', image)
        row['exif_status'] = exif[2] if exif else 'not_processed'
        if exif:
            row.update(exif_record_id=exif[0], exif_run_id=exif[1])
            raw = json.loads(exif[3]) if exif[3] else {}
            if isinstance(raw, list):
                raw = raw[0] if raw else {}
            for tag, value in raw.items():
                row['exif_raw.' + tag] = value
            normalized = json.loads(exif[4]) if exif[4] else {}
            for field, entry in normalized.get('fields', {}).items():
                value = entry.get('value') if entry else None
                if field == 'capture_timestamp':
                    row['exif_capture_timestamp'] = value.get('iso8601') if value else None
                    row['exif_timezone_known'] = value.get('timezone_known') if value else None
                else:
                    row['exif_' + field] = value
        ocr = latest("SELECT r.id,r.run_id,r.status,r.payload_json FROM processing_records r JOIN processing_runs p ON p.id=r.run_id WHERE r.image_id=? AND p.stage='ocr' ORDER BY r.rowid DESC LIMIT 1", image)
        row['ocr_status'] = ocr[2] if ocr else 'not_processed'
        if ocr:
            row.update(ocr_record_id=ocr[0], ocr_run_id=ocr[1])
            data = json.loads(ocr[3]) if ocr[3] else {}
            parsed = data.get('parsed', {})
            for field, value in parsed.get('values', {}).items():
                if field != 'temperatures':
                    row['ocr_' + field] = value
            for unit in ['F', 'C']:
                readings = [t for t in parsed.get('values', {}).get('temperatures', []) if t['unit'] == unit]
                row['ocr_temperature_' + unit.lower()] = readings[0]['value'] if readings else None
                row['ocr_temperature_' + unit.lower() + '_candidate_count'] = len(readings)
            tokens = data.get('tokens', [])
            row['ocr_text'] = ' '.join(t['text'] for t in tokens) if tokens else None
            row['ocr_min_confidence'] = min((t['confidence'] for t in tokens), default=None)
            row['ocr_profile_id'] = data.get('profile', {}).get('id')
            row['ocr_profile_version'] = data.get('profile', {}).get('version')
            row['ocr_review_required'] = parsed.get('review_required')
            row['ocr_warnings'] = '; '.join(parsed.get('warnings', [])) or None
        row['has_metadata_conflict'] = bool(connection.execute('SELECT 1 FROM metadata_resolved WHERE image_id=? AND conflict=1 LIMIT 1', (image,)).fetchone())
        row['open_review_cases'] = connection.execute("SELECT count(*) FROM reviews WHERE image_id=? AND status='open'", (image,)).fetchone()[0]
        # Long text becomes additional columns, keeping one row per image.
        expanded = {}
        for key, value in row.items():
            if isinstance(value, (dict, list, tuple)):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if isinstance(value, str) and len(value) > 30000:
                for offset in range(0, len(value), 30000):
                    expanded[key if offset == 0 else key + '__part_' + str(offset // 30000 + 1)] = value[offset:offset+30000]
            else:
                expanded[key] = value
        yield expanded


def write_analysis(connection, sheets):
    columns = set(('image_id filename exif_status exif_record_id exif_run_id '
        'exif_capture_timestamp exif_timezone_known exif_make exif_model exif_physical_camera_id '
        'exif_temperature_c exif_latitude exif_longitude exif_width exif_height exif_orientation '
        'ocr_status ocr_record_id ocr_run_id ocr_capture_timestamp ocr_date ocr_time '
        'ocr_camera_site_label ocr_brand ocr_timezone_known ocr_temperature_f ocr_temperature_c '
        'ocr_temperature_f_candidate_count ocr_temperature_c_candidate_count ocr_text '
        'ocr_min_confidence ocr_profile_id ocr_profile_version ocr_review_required ocr_warnings '
        'has_metadata_conflict open_review_cases').split())
    for row in analysis_rows(connection):
        columns.update(row)
    headers = ['image_id', 'filename'] + sorted(columns - {'image_id', 'filename'})
    if len(headers) > 16384:
        raise ValueError('Analysis Data exceeds the Excel column limit for this metadata set.')
    sheets.write('Analysis Data', headers, ([row.get(key) for key in headers] for row in analysis_rows(connection)),
                 'One row per image; latest EXIF and OCR attempts independently; raw numeric EXIF codes retained; OCR temperatures use first candidate per unit with candidate counts; long text continues in __part_N columns')
