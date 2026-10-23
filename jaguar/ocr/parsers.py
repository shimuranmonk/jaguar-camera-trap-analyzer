import re
from datetime import datetime


def parse_overlay(tokens, profile):
    # Text is retained exactly; normalization is restricted to numeric fields.
    text = " ".join(token["text"] for token in tokens if token.get("region") != "brand")
    result = {"capture_timestamp": None, "date": None, "time": None, "temperatures": [], "camera_site_label": None, "brand": None, "timezone_known": False}
    for token in tokens:
        for brand in ["Bushnell", "Moultrie"]:
            if token["confidence"] >= .90 and re.search(r"\b" + brand + r"\b", token["text"], re.I):
                result["brand"] = brand
    warnings = []
    date = re.search(r"\b(\d{2}[-/]\d{2}[-/]\d{4})\b", text)
    if not date:
        date = re.search(r"\b(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\b", text)
    if date:
        for fmt in profile.date_formats:
            try:
                result["date"] = datetime.strptime(date[1].replace("/", "-"), fmt).date().isoformat()
                break
            except ValueError:
                pass
    clock = re.search(r"\b(\d{1,2}:\d{2}(?::\d{2})?)\s*(am|pm)?\b", text, re.I)
    if clock:
        value = clock[1] + (" " + clock[2].upper() if clock[2] else "")
        for fmt in ["%I:%M:%S %p", "%I:%M %p", "%H:%M:%S", "%H:%M"]:
            try:
                result["time"] = datetime.strptime(value, fmt).time().isoformat()
                break
            except ValueError:
                pass
    if result["date"] and result["time"]:
        result["capture_timestamp"] = result["date"] + "T" + result["time"]
    temperature_pattern = r"(?<!\d)([+-]?\d(?:\s*\d){0,2}(?:\.\d+)?)\s*[°º]?\s*([FC])(?=\s|[+\-\d°º]|$|O\b)"
    for match in re.finditer(temperature_pattern, text, re.I):
        numeric = re.sub(r"\s+", "", match[1])
        value, unit = float(numeric), match[2].upper()
        result["temperatures"].append({"raw_text": match[0], "value": value, "unit": unit, "celsius": value if unit == "C" else (value - 32) * 5 / 9})
        if numeric != match[1]:
            warnings.append("OCR digit spacing was normalized; temperature requires review.")
    label = re.search(r"\b[A-Z]{1,5}[_-][A-Z]{2,6}\d{2,5}\b", text, re.I)
    if label:
        result["camera_site_label"] = label[0]
    else:
        site = re.search(r"\bNPCA\s+\d+\b", text, re.I)
        if site:
            result["camera_site_label"] = site[0]
    for key in ["capture_timestamp", "temperatures", "camera_site_label"]:
        if not result[key]:
            warnings.append(f"Missing overlay {key}.")
    temperatures = {item["unit"]: item for item in result["temperatures"]}
    if profile.id == "bushnell-band" and not {"F", "C"} <= temperatures.keys():
        warnings.append("Both printed Fahrenheit and Celsius values were expected.")
    if {"F", "C"} <= temperatures.keys() and abs(temperatures["F"]["celsius"] - temperatures["C"]["celsius"]) > 1:
        warnings.append("Printed temperature readings disagree beyond one degree Celsius.")
    if tokens and min(float(token["confidence"]) for token in tokens) < .80:
        warnings.append("Low-confidence OCR requires review.")
    if not tokens:
        warnings.append("No overlay text was recognized.")
    return {"values": result, "warnings": warnings, "review_required": bool(warnings)}
