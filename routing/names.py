"""City-name normalization shared by the geocoding script and the city search."""
import re
import unicodedata

ABBREVIATIONS = {"saint": "st", "sainte": "ste", "fort": "ft", "mount": "mt",
                 "north": "n", "south": "s", "east": "e", "west": "w"}
# Census names end with their type, e.g. "Abbeville city", "Abanda CDP", "Mahwah township".
PLACE_TYPE = re.compile(r"\s+(city|town|township|village|borough|plantation|municipality|CDP)$")
# Consolidated city-counties, e.g. "Nashville-Davidson metropolitan government (balance)".
CONSOLIDATED = re.compile(r"county|government|\(balance\)", re.IGNORECASE)


def normalize(name):
    """Reduce spelling variants ("St. Paul", "Saint Paul", "Cañon City") to one key."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = re.sub(r"[.'\-]", " ", name.lower()).split()
    return "".join(ABBREVIATIONS.get(word, word) for word in words)


def census_names(raw_name, is_subdivision=False):
    """Return (name, is_alias) pairs a city could be called for this Census entry."""
    name = raw_name.replace(" (balance)", "")
    has_type = PLACE_TYPE.search(name)
    if is_subdivision and not has_type:
        return []  # Statistical areas (CCDs, precincts, districts) are too large to stand in for a town.
    name = PLACE_TYPE.sub("", name)
    names = [(name, False)]
    if CONSOLIDATED.search(raw_name):
        names.append((re.split(r"[-/,]", name)[0], True))  # "Nashville-Davidson ..." -> "Nashville"
    if name.endswith(" City"):
        names.append((name[: -len(" City")], True))  # "Boise City" -> "Boise"
    return names
