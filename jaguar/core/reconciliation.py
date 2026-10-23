import hashlib
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from jaguar.ingest.service import import_lock, now

FIELDS = {"capture_timestamp", "make", "model", "physical_camera_id", "site_label", "temperature_c", "latitude", "longitude", "width", "height", "orientation"}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def validate_value(field, value):
    if field not in FIELDS:
        raise ValueError("The selected field cannot be corrected.")
    if value is None:
        return None
    if field == "capture_timestamp":
        stamp = datetime.fromisoformat(str(value))
        return {"iso8601": stamp.isoformat(), "timezone_known": stamp.tzinfo is not None}
    if field in {"temperature_c", "latitude", "longitude", "width", "height", "orientation"}:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("A finite numeric value is required.")
        if field == "latitude" and not -90 <= parsed <= 90 or field == "longitude" and not -180 <= parsed <= 180:
            raise ValueError("Coordinate is outside its valid range.")
        if field in {"width", "height", "orientation"}:
            if parsed <= 0 or not parsed.is_integer() or field == "orientation" and parsed > 8:
                raise ValueError("A valid positive integer is required.")
            return int(parsed)
        return parsed
    return str(value).strip() or None


def correction(database, image_id, field, value, reason, actor="local operator", revert=False):
    if not reason.strip():
        raise ValueError("A correction reason is required.")
    if field not in FIELDS:
        raise ValueError("Unsupported field.")
    parsed = validate_value(field, value) if not revert else None
    with import_lock(database), closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        old = connection.execute("SELECT value_json FROM metadata_resolved WHERE image_id=? AND field=?", (image_id, field)).fetchone()
        event_id = str(uuid4())
        with connection:
            connection.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?,?)", (event_id, image_id, field, old[0] if old else None, canonical(parsed), actor, reason.strip(), "revert" if revert else "set", now()))
            connection.execute("INSERT INTO human_overrides VALUES (?,?,?,?,?) ON CONFLICT(image_id,field) DO UPDATE SET value_json=excluded.value_json,event_id=excluded.event_id,active=excluded.active", (image_id, field, canonical(parsed), event_id, 0 if revert else 1))
            # Invalidate the displayed auto-resolution; the review action runs
            # reconciliation for this image before presenting the final value.
            if not revert:
                connection.execute("INSERT INTO metadata_resolved VALUES (?,?,?,?,?,?,?) ON CONFLICT(image_id,field) DO UPDATE SET value_json=excluded.value_json,source=excluded.source,provenance_json=excluded.provenance_json,updated_at=excluded.updated_at", (image_id, field, canonical(parsed), "human", canonical({"audit_event_id": event_id}), 0, now()))
            else:
                connection.execute("DELETE FROM metadata_resolved WHERE image_id=? AND field=?", (image_id, field))


class Reconciler:
    version = "reconciliation-1"
    no_source = True

    def __init__(self, timestamp_tolerance=0, temperature_tolerance=1):
        if timestamp_tolerance < 0 or temperature_tolerance < 0:
            raise ValueError("Tolerances cannot be negative.")
        self.timestamp_tolerance = timestamp_tolerance
        self.temperature_tolerance = temperature_tolerance
        self.version = f"reconciliation-1:{timestamp_tolerance}:{temperature_tolerance}"

    def extract_for_image(self, connection, image_id):
        observations, selected, reviews, input_ids = [], {}, [], []
        def observe(record_id, field, source, value, raw, confidence=None):
            identity = hashlib.sha256(canonical([record_id, field, source, raw]).encode()).hexdigest()
            observation = {"id": identity, "record_id": record_id, "field": field, "source": source, "value": value, "raw": raw, "confidence": confidence}
            observations.append(observation)
            return observation
        exif = connection.execute("SELECT id,status,normalized_json,error FROM exif_records WHERE image_id=? ORDER BY rowid DESC LIMIT 1", (image_id,)).fetchone()
        if exif:
            input_ids.append(exif[0])
            if exif[1] == "succeeded":
                data = json.loads(exif[2])
                for item in data["observations"]:
                    observe(exif[0], item["field"], "exif:" + item["tag"], item["parsed_value"], item["raw_value"])
                for field, candidate in data["fields"].items():
                    if candidate:
                        selected[field] = {"value": candidate["value"], "source": "exif:" + candidate["source_tag"], "record_id": exif[0], "conflict": False}
                if data["warnings"]:
                    reviews.append(("exif_warning", data["warnings"]))
            else:
                reviews.append(("exif_failure", exif[3]))
        ocr = connection.execute("SELECT r.id,r.status,r.payload_json,r.error FROM processing_records r JOIN processing_runs p ON p.id=r.run_id WHERE r.image_id=? AND p.stage='ocr' ORDER BY r.rowid DESC LIMIT 1", (image_id,)).fetchone()
        if ocr:
            input_ids.append(ocr[0])
            if ocr[1] == "succeeded":
                payload = json.loads(ocr[2])
                values = payload["parsed"]["values"]
                mapping = {"site_label": values.get("camera_site_label"), "make": values.get("brand")}
                if values.get("capture_timestamp"):
                    mapping["capture_timestamp"] = {"iso8601": values["capture_timestamp"], "timezone_known": False}
                temperatures = values.get("temperatures", [])
                for temp in temperatures:
                    observe(ocr[0], "temperature_c", "ocr:" + temp["unit"], temp["celsius"], temp)
                if temperatures:
                    mapping["temperature_c"] = next((t["celsius"] for t in temperatures if t["unit"] == "C"), temperatures[0]["celsius"])
                confidence = min((t["confidence"] for t in payload["tokens"]), default=0)
                for field, value in mapping.items():
                    if value is None:
                        continue
                    observe(ocr[0], field, "ocr", value, payload["tokens"], confidence)
                    if field in selected:
                        previous = selected[field]["value"]
                        conflict = self.different(field, previous, value)
                        selected[field]["conflict"] = conflict
                        if conflict:
                            reviews.append(("metadata_conflict:" + field, {"exif": previous, "ocr": value}))
                    else:
                        selected[field] = {"value": value, "source": "ocr", "record_id": ocr[0], "conflict": False}
                if payload["parsed"]["review_required"]:
                    reviews.append(("ocr_review", payload["parsed"]["warnings"]))
            else:
                reviews.append(("ocr_failure", ocr[3]))
        detection = connection.execute("SELECT r.id,r.status,r.payload_json,r.error FROM processing_records r JOIN processing_runs p ON p.id=r.run_id WHERE r.image_id=? AND p.stage='detect' ORDER BY r.rowid DESC LIMIT 1", (image_id,)).fetchone()
        if detection:
            input_ids.append(detection[0])
            if detection[1] == "succeeded":
                boxes = json.loads(detection[2])["detections"]
                if any(b["class"] in {"person", "vehicle"} for b in boxes):
                    reviews.append(("person_or_vehicle", [b["class"] for b in boxes]))
                if any(b["confidence"] < .4 for b in boxes):
                    reviews.append(("low_confidence_detection", "An accepted box has confidence below 0.4."))
                if not boxes:
                    reviews.append(("no_accepted_detection", "No accepted box does not confirm an empty image."))
            else:
                reviews.append(("detection_failure", detection[3]))
        if "capture_timestamp" not in selected:
            reviews.append(("missing_capture_timestamp", "No valid capture timestamp was extracted."))
        overrides = connection.execute("SELECT field,value_json,event_id FROM human_overrides WHERE image_id=? AND active=1", (image_id,)).fetchall()
        for field, value, event_id in overrides:
            selected[field] = {"value": json.loads(value), "source": "human", "audit_event_id": event_id, "conflict": selected.get(field, {}).get("conflict", False)}
            reviews = [item for item in reviews if item[0] != "metadata_conflict:" + field]
        reviews = [item for item in reviews if item[0] != "missing_capture_timestamp"]
        if not selected.get("capture_timestamp", {}).get("value"):
            reviews.append(("missing_capture_timestamp", "No selected valid capture timestamp is available."))
        return {"observations": observations, "selected": selected, "reviews": reviews, "inputs": input_ids,
                "settings": {"timestamp_tolerance_seconds": self.timestamp_tolerance, "temperature_tolerance_c": self.temperature_tolerance}}

    def different(self, field, left, right):
        if field == "capture_timestamp":
            a,b = datetime.fromisoformat(left["iso8601"]), datetime.fromisoformat(right["iso8601"])
            if (a.tzinfo is None) != (b.tzinfo is None):
                return True
            return abs((a-b).total_seconds()) > self.timestamp_tolerance
        if field == "temperature_c":
            return abs(float(left)-float(right)) > self.temperature_tolerance
        return str(left).strip().casefold() != str(right).strip().casefold()

    def save(self, connection, image_id, payload):
        for item in payload["observations"]:
            connection.execute("INSERT OR IGNORE INTO metadata_observations VALUES (?,?,?,?,?,?,?,?,?)", (item["id"], image_id, item["field"], item["source"], canonical(item["value"]), canonical(item["raw"]), item["confidence"], item["record_id"], now()))
        for field in FIELDS:
            item = payload["selected"].get(field)
            connection.execute("INSERT INTO metadata_resolved VALUES (?,?,?,?,?,?,?) ON CONFLICT(image_id,field) DO UPDATE SET value_json=excluded.value_json,source=excluded.source,provenance_json=excluded.provenance_json,conflict=excluded.conflict,updated_at=excluded.updated_at", (image_id, field, canonical(item["value"] if item else None), item["source"] if item else None, canonical(item), int(item.get("conflict", False)) if item else 0, now()))
        active_keys = []
        for reason, detail in payload["reviews"]:
            fingerprint = hashlib.sha256(canonical([reason, detail, payload["inputs"]]).encode()).hexdigest()
            active_keys.append(fingerprint)
            connection.execute("INSERT OR IGNORE INTO reviews VALUES (?,?,?,?,?,'open',?,NULL)", (str(uuid4()), image_id, reason, fingerprint, canonical(detail), now()))
            connection.execute("UPDATE reviews SET status='open',resolved_at=NULL WHERE image_id=? AND fingerprint=? AND status='superseded'", (image_id, fingerprint))
        # Prior evidence cases remain in history; superseded open cases are closed.
        connection.execute("UPDATE reviews SET status='superseded',resolved_at=? WHERE image_id=? AND status='open' AND fingerprint NOT IN (SELECT value FROM json_each(?))", (now(), image_id, canonical(active_keys)))


def set_review_status(database, review_id, status, actor="local operator"):
    if status not in {"open", "resolved", "deferred"}:
        raise ValueError("Invalid review status.")
    with import_lock(database), closing(sqlite3.connect(database)) as connection:
        row = connection.execute("SELECT image_id,status FROM reviews WHERE id=?", (review_id,)).fetchone()
        if not row:
            raise ValueError("Review case was not found.")
        with connection:
            connection.execute("UPDATE reviews SET status=?,resolved_at=? WHERE id=?", (status, now() if status == "resolved" else None, review_id))
            connection.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?,?)", (str(uuid4()), row[0], "review:" + review_id, canonical(row[1]), canonical(status), actor, "Review status changed.", "review", now()))
