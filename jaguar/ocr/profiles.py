from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Profile:
    id: str
    version: int
    brand: str | None
    regions: tuple
    date_formats: tuple = ("%m-%d-%Y", "%d %b %Y")

    def snapshot(self):
        return asdict(self)


PROFILES = {
    "bushnell-band": Profile("bushnell-band", 2, "Bushnell", (("band", 0, .925, 1, 1), ("brand", 0, .82, .12, 1))),
    "generic-edges": Profile("generic-edges", 1, None, (("bottom", 0, .80, 1, 1), ("top", 0, 0, 1, .20))),
    "moultrie-band": Profile("moultrie-band", 1, "Moultrie", (("bottom", 0, .82, 1, 1),)),
}


def choose_profile(make=None, assigned=None):
    if assigned:
        if assigned not in PROFILES:
            raise ValueError("The assigned OCR profile is unavailable.")
        return PROFILES[assigned], "manual"
    make = str(make or "").strip().casefold()
    if make == "bushnell":
        return PROFILES["bushnell-band"], "exif_make"
    if make == "moultrie":
        return PROFILES["moultrie-band"], "exif_make"
    return PROFILES["generic-edges"], "generic_fallback"
