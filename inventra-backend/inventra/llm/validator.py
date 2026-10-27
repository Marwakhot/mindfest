"""Explanation validator (design doc section 9.3).

Every number in the LLM text must equal a fact it cites (within rounding); every causal
clause must cite a reason ID that exists; Assumption / Unverified facts must be called
that in the same sentence; no SKU or location outside the fact set may appear.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

ID_RE = re.compile(r"\[([^\[\]]+)\]")
CITE_TOKEN = re.compile(r"\b([FR]\d+)\b")
CODE_RE = re.compile(r"\b(?:[A-Z]{2,4}-\d{2,4}|PO-\d+|SIG-\d+|R\d|P10|P50|P90)\b")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
NUM_RE = re.compile(r"(?<![A-Za-z])-?\d+(?:\.\d+)?")
CAUSAL = re.compile(r"\b(because|due to|since|as a result|caused|causes|cause|so that|therefore|driven by|owing to|why)\b", re.I)
LOCATIONS = ("Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Fujairah", "Ras Al Khaimah", "Umm Al Quwain", "Al Ain")

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000, "half": 0.5,
    "double": 2, "twice": 2, "triple": 3, "dozen": 12,
}
WORD_RE = re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r")\b", re.I)


def split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\[])", text.strip())
    return [p.strip() for p in parts if p.strip()]


def normalise(s: str) -> str:
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)   # thousands separators
    s = s.replace("AED", " AED ").replace("%", " % ")
    return s


def _matches(n: float, value: Any) -> bool:
    if isinstance(value, str):  # a text fact (e.g. an option label) vouches for the numbers written in it
        return any(_matches(n, float(x)) for x in NUM_RE.findall(DATE_RE.sub(" ", CODE_RE.sub(" ", normalise(value)))))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    s = f"{n}"
    dec = len(s.split(".")[1]) if "." in s else 0
    return abs(round(float(value), dec) - n) < 1e-9 or abs(float(value) - n) <= 0.5 * 10 ** (-dec) + 1e-9


def validate(text: str, facts: Dict[str, Any], allow: List[Any] = ()) -> Tuple[bool, List[str], int]:
    """Returns (passed, errors, figures_checked)."""
    fact_by_id = {f["id"]: f for f in facts.get("facts", [])}
    reason_ids = {r["id"] for r in facts.get("reasons", [])}
    corpus = " ".join(str(f.get("label", "")) + " " + str(f.get("value", "")) for f in facts.get("facts", []))
    corpus += " " + " ".join(r.get("text", "") for r in facts.get("reasons", []))
    allowed_codes = set(CODE_RE.findall(corpus))
    allowed_dates = set(DATE_RE.findall(corpus))
    allow_nums = {float(a) for a in allow}
    errors: List[str] = []
    checked = 0
    sentences = split_sentences(text)
    if len(sentences) != 3:
        errors.append(f"expected 3 sentences, got {len(sentences)}")
    for i, raw in enumerate(sentences, 1):
        cited = set()
        for grp in ID_RE.findall(raw):
            cited.update(CITE_TOKEN.findall(grp))
        for c in cited:
            if c not in fact_by_id and c not in reason_ids:
                errors.append(f"sentence {i}: cites unknown ID {c}")
        body = ID_RE.sub(" ", raw)
        for code in CODE_RE.findall(body):
            if code not in allowed_codes and code not in ("P10", "P50", "P90"):
                errors.append(f"sentence {i}: mentions {code}, which is not in the fact set")
        for loc in LOCATIONS:
            if re.search(r"\b" + loc + r"\b", body) and loc not in corpus:
                errors.append(f"sentence {i}: mentions {loc}, which is not in the fact set")
        for d in DATE_RE.findall(body):
            if d not in allowed_dates:
                errors.append(f"sentence {i}: date {d} is not in the fact set")
        scrubbed = DATE_RE.sub(" ", CODE_RE.sub(" ", normalise(body)))
        cited_values = [fact_by_id[c]["value"] for c in cited if c in fact_by_id]
        for m in NUM_RE.findall(scrubbed):
            n = float(m)
            checked += 1
            if n in allow_nums:
                continue
            if not any(_matches(n, v) for v in cited_values):
                errors.append(f"sentence {i}: number {m} does not match any fact it cites")
        for w in WORD_RE.findall(scrubbed):
            checked += 1
            n = NUMBER_WORDS[w.lower()]
            if n not in allow_nums and not any(_matches(n, v) for v in cited_values):
                errors.append(f"sentence {i}: number word '{w}' must be written in digits and cite a fact")
        if CAUSAL.search(body) and not any(c in reason_ids for c in cited):
            errors.append(f"sentence {i}: a reason or cause must cite a reason ID")
        tagged = [fact_by_id[c] for c in cited if c in fact_by_id and fact_by_id[c].get("tag") in ("Assumption", "Unverified")]
        tagged += [r for r in facts.get("reasons", []) if r["id"] in cited and r.get("tag") in ("Assumption", "Unverified")]
        for f in tagged:
            word = f["tag"].lower()
            if word not in body.lower():
                errors.append(f"sentence {i}: cites {f['id']} tagged {f['tag']} but does not say '{word}'")
    return (not errors), errors, checked
