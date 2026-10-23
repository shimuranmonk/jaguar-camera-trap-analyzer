import json
import math
import re
import subprocess
from datetime import datetime
from pathlib import Path


class ExtractionError(ValueError):
    def __init__(self, message, raw_json=None, stderr=None):
        super().__init__(message)
        self.raw_json = raw_json
        self.stderr = stderr


class ExifTool:
    def __init__(self, executable, timeout=30):
        self.executable = str(Path(executable).resolve())
        self.timeout = timeout

    def invoke(self, arguments):
        try:
            result = subprocess.run([self.executable, "-config", "", *arguments],
                capture_output=True, timeout=self.timeout, shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as error:
            raise ExtractionError("ExifTool timed out.") from error
        except OSError as error:
            raise ExtractionError("ExifTool could not be started; its executable and companion folder require checking.") from error
        output = result.stdout.decode("utf-8-sig", errors="strict")
        return result.returncode, output, result.stderr.decode("utf-8", errors="replace")

    def version(self):
        code, output, _ = self.invoke(["-ver"])
        if code or not re.fullmatch(r"\d+\.\d+", output.strip()):
            raise ExtractionError("ExifTool version could not be verified.")
        return output.strip()

    def extract(self, path):
        code, raw, stderr = self.invoke(["-j", "-G1", "-a", "-s", "-n", "-charset", "filename=UTF8", "--", str(Path(path).resolve())])
        try:
            records = json.loads(raw)
            if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
                raise ValueError()
        except (ValueError, TypeError) as error:
            raise ExtractionError("ExifTool returned invalid JSON.", raw, stderr) from error
        record = records[0]
        errors = [value for key, value in record.items() if key.split(":")[-1] == "Error"]
        return {"raw_json": raw, "record": record, "stderr": stderr,
                "error": "ExifTool reported an extraction error." if code or errors else None}


def number(value):
    if isinstance(value, bool):
        raise ValueError()
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError()
    return parsed


def normalize(record):
    fields = {name: None for name in ["capture_timestamp", "make", "model", "physical_camera_id", "temperature_c", "latitude", "longitude", "width", "height", "orientation"]}
    observations = []
    warnings = [str(value) for tag, value in record.items() if tag.split(":")[-1] == "Warning"]

    def select(field, tags, parser=lambda value: None if value is None else str(value).strip() or None):
        for tag in tags:
            if tag not in record:
                continue
            raw = record[tag]
            try:
                parsed = parser(raw)
            except (ValueError, TypeError, OverflowError):
                parsed = None
                warnings.append(f"Invalid {field}: {tag}")
            observations.append({"field": field, "tag": tag, "raw_value": raw, "parsed_value": parsed})
            if fields[field] is None and parsed is not None:
                fields[field] = {"value": parsed, "source_tag": tag}

    def timestamp(value, tag):
        text = str(value).strip()
        if not re.match(r"^\d{4}:\d{2}:\d{2} ", text):
            raise ValueError()
        text = text[:10].replace(":", "-") + "T" + text[11:]
        stamp = datetime.fromisoformat(text)
        if stamp.tzinfo is None:
            offset_tag = "ExifIFD:OffsetTimeDigitized" if tag == "ExifIFD:CreateDate" else "ExifIFD:OffsetTimeOriginal"
            offset = record.get(offset_tag)
            if offset and re.fullmatch(r"[+-]\d{2}:\d{2}", str(offset)):
                stamp = datetime.fromisoformat(stamp.isoformat() + str(offset))
        return {"iso8601": stamp.isoformat(), "timezone_known": stamp.tzinfo is not None}

    for tag in ["ExifIFD:DateTimeOriginal", "IFD0:DateTimeOriginal", "ExifIFD:CreateDate"]:
        select("capture_timestamp", [tag], lambda value, tag=tag: timestamp(value, tag))
    if fields["capture_timestamp"] and not fields["capture_timestamp"]["value"]["timezone_known"]:
        warnings.append("Capture timezone is unspecified.")
    select("make", ["IFD0:Make"])
    select("model", ["IFD0:Model"])
    select("physical_camera_id", ["ExifIFD:BodySerialNumber", "ExifIFD:SerialNumber", "MakerNotes:SerialNumber", "Canon:SerialNumber", "Nikon:SerialNumber", "Reconyx:SerialNumber"])

    def temperature(value):
        return number(value)
    # The standard EXIF AmbientTemperature tag is defined in degrees Celsius.
    select("temperature_c", ["ExifIFD:AmbientTemperature"], temperature)
    for tag, value in record.items():
        if tag.split(":")[-1] in {"CameraTemperature", "Temperature", "AmbientTemperature"} and tag != "ExifIFD:AmbientTemperature":
            match = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*°?\s*([CF])\s*", str(value), re.I)
            parsed = None
            if match:
                original, unit = number(match[1]), match[2].upper()
                parsed = original if unit == "C" else (original - 32) * 5 / 9
            observations.append({"field": "temperature_c", "tag": tag, "raw_value": value, "parsed_value": parsed})
            if parsed is not None and fields["temperature_c"] is None:
                fields["temperature_c"] = {"value": parsed, "source_tag": tag}
            if parsed is None:
                warnings.append(f"Temperature unit requires review: {tag}")

    def coordinate(value, axis):
        parsed = number(value)
        ref = record.get("GPS:GPSLatitudeRef" if axis == "latitude" else "GPS:GPSLongitudeRef")
        if ref:
            ref = str(ref).upper()
            valid = {"N", "S"} if axis == "latitude" else {"E", "W"}
            if ref not in valid:
                raise ValueError()
            parsed = abs(parsed) * (-1 if ref in {"S", "W"} else 1)
        elif parsed >= 0:
            raise ValueError()
        limit = 90 if axis == "latitude" else 180
        if not -limit <= parsed <= limit:
            raise ValueError()
        return parsed
    select("latitude", ["GPS:GPSLatitude"], lambda value: coordinate(value, "latitude"))
    select("longitude", ["GPS:GPSLongitude"], lambda value: coordinate(value, "longitude"))
    def positive(value):
        parsed = number(value)
        if parsed <= 0 or not parsed.is_integer():
            raise ValueError()
        return int(parsed)
    select("width", ["File:ImageWidth", "ExifIFD:ExifImageWidth", "IFD0:ImageWidth"], positive)
    select("height", ["File:ImageHeight", "ExifIFD:ExifImageHeight", "IFD0:ImageHeight"], positive)
    def orientation(value):
        parsed = positive(value)
        if parsed > 8:
            raise ValueError()
        return parsed
    select("orientation", ["IFD0:Orientation"], orientation)
    return {"fields": fields, "observations": observations, "warnings": warnings}
