"""Resident and crew messages in English and Hindi.

The templates are deterministic so the rule-based agent (and the tests) never
depend on a model. The Bedrock agent may rephrase the summary, but alert text
sent to residents always comes from here: wording that tells people when to
drink their water must not be improvised.
"""

from __future__ import annotations

import math

from ..incident import pct
from ..locate.advisories import ampm

LANGS = ("en", "hi")


def hindi_time(t_min: float) -> str:
    """Minutes since midnight -> 'शाम 7:40 बजे' (part of day + 12-hour clock)."""
    m = int(math.ceil(t_min - 1e-9)) % 1440
    h, mm = divmod(m, 60)
    if 4 <= h < 12:
        part = "सुबह"
    elif 12 <= h < 16:
        part = "दोपहर"
    elif 16 <= h < 20:
        part = "शाम"
    else:
        part = "रात"
    return f"{part} {h % 12 or 12}:{mm:02d} बजे"


def advisory_text(status: str, safe_after: float | None, lang: str = "en") -> str:
    if lang not in LANGS:
        raise ValueError(f"lang must be one of {LANGS}")
    if status == "no_safe_window" or safe_after is None:
        if lang == "hi":
            return ("अपस्ट्रीम जल चेतावनी: आज की सप्लाई का पानी दूषित रह सकता है। पीने और खाना बनाने के लिए "
                    "पहले से भरा हुआ या उबला पानी इस्तेमाल करें, और अगली सूचना तक पीने का पानी उबालकर पिएँ।")
        return ("Upstream water alert: today's supply may stay contaminated. Use stored or boiled water for "
                "drinking and cooking, and boil all drinking water until the all-clear.")
    if lang == "hi":
        return (f"अपस्ट्रीम जल चेतावनी: आज पीने का पानी {hindi_time(safe_after)} के बाद भरें; पहला पानी सफ़ाई के "
                "लिए इस्तेमाल करें। अगली सूचना तक पीने का पानी उबालकर पिएँ।")
    return (f"Upstream water alert: today, fill drinking water after {ampm(safe_after)}; use the first fill for "
            "cleaning. Boil drinking water until the all-clear.")


def strip_request_text(lang: str = "en") -> str:
    if lang == "hi":
        return ("अपस्ट्रीम: कृपया अगली सप्लाई पर अपने नल के पहले गिलास पानी की कार्ड पर फ़ोटो भेजें। "
                "इससे गंदे पानी का स्रोत जल्दी मिलेगा।")
    return ("Upstream: please photograph your first glass of water on the card at the next supply. "
            "It helps us find the source of the contamination quickly.")


def crew_brief(incident: dict, lang: str = "en") -> str:
    dig = incident.get("dig") or {}
    lat, lon = dig.get("lat"), dig.get("lon")
    where = f"{lat:.5f}, {lon:.5f}" if lat is not None and lon is not None else "n/a"
    region = incident.get("region_length_m")
    n_homes = incident.get("n_advisories", 0)
    p = pct(incident.get("p_breach"))
    if lang == "hi":
        reg = f"{region:.0f} मीटर" if region is not None else "अज्ञात"
        return (f"घटना {incident['id']}: पाइप में सीवेज रिसाव की संभावना {p}। खुदाई का सबसे संभावित स्थान {where}; "
                f"90% क्षेत्र लगभग {reg} पाइप। {n_homes} घरों को सलाह भेजी जाएगी।")
    reg = f"{region:.0f} m" if region is not None else "unknown"
    return (f"Incident {incident['id']}: {p} probability of a sewage breach. Most likely dig spot {where}; "
            f"90% region about {reg} of pipe. {n_homes} homes will be advised.")
