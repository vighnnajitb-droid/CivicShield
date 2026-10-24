"""Local/mock AI service. Used by the n8n AI workflow when no LLM credentials are configured.
Rule-based, deterministic, advisory only. It never assesses guilt."""
import re

LABEL = "AI-assisted analysis \u2014 requires human review."
DISCLAIMER = ("This analysis is advisory only. It does not determine guilt, wrongdoing or the truth of any allegation. "
              "The citizen must review and edit it before submission.")
GUILT_WORDS = re.compile(r"\b(guilty|corrupt official|criminal|crook|thief|proved|proven|convicted)\b", re.I)
ROLES = ["officer", "inspector", "clerk", "constable", "sub-inspector", "engineer", "contractor", "supervisor", "councillor",
         "magistrate", "tehsildar", "doctor", "nurse", "head constable", "revenue inspector", "village administrative officer",
         "sanitary inspector", "driver", "peon", "assistant", "secretary", "commissioner"]
MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
DATE_RE = re.compile(rf"\b(\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{2,4}}|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS})\.?,?\s*\d{{0,4}}|(?:{MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s*\d{{0,4}})\b", re.I)
TIME_RE = re.compile(r"\b(\d{1,2}[:.]\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm)|morning|afternoon|evening|night|noon|midnight)\b", re.I)
MONEY_RE = re.compile(r"(?:rs\.?|inr|\u20b9)\s?\d[\d,]*(?:\.\d+)?", re.I)
LOC_RE = re.compile(r"\b(?:at|near|in|outside|inside|opposite)\s+((?:the\s+)?[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+){0,4})")
NAME_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Shri|Smt|Thiru)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?")
ORG_RE = re.compile(r"\b[A-Z][\w&-]+(?:\s+[A-Z][\w&-]+)*\s+(?:Office|Department|Station|Corporation|Board|Hospital|Municipality|Panchayat|Authority)\b")

def _sentences(t):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", t) if s.strip()]

def _uniq(xs):
    seen, out = set(), []
    for x in xs:
        k = x.strip().lower()
        if k and k not in seen:
            seen.add(k); out.append(x.strip())
    return out

def neutralise(text):
    """Strip guilt-determining language from any AI output."""
    return GUILT_WORDS.sub("[alleged]", text)

def analyze(description, evidence=None, category=None, location=None, mode="draft"):
    evidence = evidence or []
    text = description or ""
    low = text.lower()
    sents = _sentences(text)
    dates = _uniq(m.group(0) for m in DATE_RE.finditer(text))
    times = _uniq(m.group(0) for m in TIME_RE.finditer(text))
    money = _uniq(m.group(0) for m in MONEY_RE.finditer(text))
    roles = _uniq(r for r in ROLES if re.search(rf"\b{re.escape(r)}s?\b", low))
    people = _uniq(m.group(0) for m in NAME_RE.finditer(text))
    orgs = _uniq(m.group(0) for m in ORG_RE.finditer(text))
    locs = _uniq(([location] if location else []) + [m.group(1) for m in LOC_RE.finditer(text)])
    timeline = []
    for s in sents:
        d = DATE_RE.search(s)
        t = TIME_RE.search(s)
        if d or t:
            timeline.append({"when": " ".join(x.group(0) for x in (d, t) if x), "event": neutralise(s)})
    missing = []
    if not dates: missing.append("Date of the incident")
    if not times: missing.append("Approximate time of the incident")
    if not locs: missing.append("Exact location")
    if not roles and not people: missing.append("Role or name of the official(s)/office involved")
    if not evidence: missing.append("Supporting evidence (photo, document, receipt, recording)")
    if len(text.split()) < 30: missing.append("More detail on what happened, step by step")
    summary = neutralise(" ".join(sents[:2])[:500]) if sents else ""
    report = {
        "incident_summary": summary,
        "alleged_conduct": f"The citizen alleges the following (unverified): {summary}" if summary else "",
        "entities": {"people": people, "organisations": orgs, "amounts": money},
        "date_time": {"dates": dates, "times": times},
        "location": locs[0] if locs else None, "locations_detected": locs,
        "involved_roles": roles, "timeline": timeline,
        "missing_information": missing,
        "evidence_listed": [{"evidence_id": e.get("id"), "filename": e.get("original_filename"), "sha256": e.get("sha256")} for e in evidence],
    }
    urgency = "high" if re.search(r"\b(threat|assault|violence|medical|emergency|life|child|bribe|extort)\w*", low) else "normal"
    return {"label": LABEL, "disclaimer": DISCLAIMER, "generated_by": "local-mock-ai", "mode": mode,
            "advisory_priority": urgency, "category_hint": category, "report": report}

def normalise_llm(raw: dict) -> dict:
    """Guard-rail wrapper for any LLM output (called by n8n for the real-LLM branch too)."""
    import json
    s = neutralise(json.dumps(raw))
    out = json.loads(s)
    out["label"], out["disclaimer"] = LABEL, DISCLAIMER
    return out
