"""Turn numbered posting lines into cited must-haves, a prep brief, mock questions and an answer scorecard.

Everything here is deterministic: Cubbie phrases the results, but every skill, fact and score traces back to a
posting line or to a phrase in the student's answer.
"""

import json
import re
from collections import Counter
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"

# ---------------------------------------------------------------- dictionaries

_TERMS = None
_QUESTIONS = None


def terms():
    global _TERMS
    if _TERMS is None:
        _TERMS = json.loads((ASSETS / "terms.json").read_text(encoding="utf-8"))["terms"]
        for t in _TERMS:
            t["_not"] = re.compile(t["not"], re.I) if t.get("not") else None
            if "regex" in t:
                t["_re"] = re.compile(t["regex"])
            else:
                alts = "|".join(re.escape(a) for a in sorted(t["aliases"], key=len, reverse=True))
                t["_re"] = re.compile(rf"(?<![a-z0-9])(?:{alts})(?![a-z0-9])")
    return _TERMS


def questions():
    global _QUESTIONS
    if _QUESTIONS is None:
        _QUESTIONS = json.loads((ASSETS / "questions.json").read_text(encoding="utf-8"))
    return _QUESTIONS


def term_info(name):
    return next((t for t in terms() if t["term"] == name), None)


def slug(text, n=40):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:n].strip("-") or "x"


# ---------------------------------------------------------------- sections of a posting

NICE_HDR = re.compile(r"(prefer|nice[- ]to[- ]have|bonus|\bplus\b|desired|extra credit|not required|good to have)")
MUST_HDR = re.compile(r"(requirement|qualification|must[- ]have|you have|you'?ll need|you will need|what you need|"
                      r"looking for|what we look for|who you are|about you|minimum|basic|required|skills|"
                      r"you bring|you'?ll bring|ideal candidate|you might be a fit|you may be a fit|\brequire\b)")
ROLE_HDR = re.compile(r"(responsib|what you'?ll do|what you will do|the role|the job|about the (team|role|job|position)|"
                      r"your impact|day[- ]to[- ]day|in this role|you will|the opportunity|what you'?ll work on|"
                      r"job description|^description|overview|summary|the team|projects|"
                      r"about (the|this) (role|internship|program|position|job))")
ABOUT_HDR = re.compile(r"(^about (?!(the|this) (role|team|job|position|opportunity|internship|program|intern|co-?op))|who we are|^our (mission|story|company|values|culture)|"
                       r"benefits|perks|^pay\b|pay and benefits|compensation|salary|equal (employment )?opportunity|eeo|"
                       r"diversity|accommodation|our commitment|why join|life at|office expectations|hybrid work|"
                       r"privacy|disclaimer|^job details)")
BOILERPLATE = re.compile(r"(equal (employment )?opportunit|without regard to|regardless of|race, (color|religion)|"
                         r"gender identity|sexual orientation|national origin|protected veteran|veteran status|"
                         r"disabilit(y|ies)|reasonable accommodation|e-?verify|pay transparency|benefits (include|package)|"
                         r"401\(k\)|paid time off|medical, dental|background check|drug (test|screen)|"
                         r"applicants (will|must|are)|we are committed to|affirmative action|privacy (notice|policy)|"
                         r"accommodation|paid (?:time off|holidays|sick)|sick (?:leave|time)|\bpto\b|vacation days|"
                         r"parental leave)", re.I)
INLINE_NICE = re.compile(r"\b(preferred|a plus|is a plus|are a plus|nice to have|bonus|ideally|not required)\b", re.I)


def classify_heading(line):
    """Section for a heading line, or None when the line is ordinary content."""
    if ":" in line and line.split(":", 1)[1].strip():
        return None
    text = line.strip().rstrip(":").strip().lower()
    if line.startswith("- ") or len(text) > 70 or (text.endswith(".") and not line.endswith(":")):
        return None
    if not line.endswith(":") and (len(text.split()) > 5 or any(
            t["_re"].search(line if "regex" in t else text) for t in terms())):
        return None  # "You will build APIs in Python" is content, not a heading
    for name, rx in (("about", ABOUT_HDR), ("nice", NICE_HDR), ("must", MUST_HDR), ("role", ROLE_HDR)):
        if rx.search(text):
            return name
    return "keep" if line.endswith(":") else None


_SECTIONS = {}


def sections(lines):
    """Section of each line: intro, about, role, must or nice. Heading lines are tagged 'heading'. Cached."""
    key = (id(lines), len(lines))
    if key in _SECTIONS and _SECTIONS[key][0] is lines:
        return _SECTIONS[key][1]
    out = _sections(lines)
    _SECTIONS[key] = (lines, out)
    return out


def _sections(lines):
    out, current = [], "intro"
    for line in lines:
        kind = classify_heading(line)
        if kind and kind != "keep":
            current = kind
            out.append("heading")
        elif kind == "keep":
            out.append("heading")
        elif BOILERPLATE.search(line):
            out.append("legal")
        else:
            out.append("nice" if current in ("must", "role", "intro") and INLINE_NICE.search(line) else current)
    return out


def quote(line, match_start=None, limit=140):
    """The line, or a window of it around the match, cut at word boundaries."""
    if line.startswith("- "):
        line = line[2:]
        match_start = None if match_start is None else max(0, match_start - 2)
    if len(line) <= limit:
        return line
    start = 0 if match_start is None else max(0, min(match_start - limit // 3, len(line) - limit))
    end = min(len(line), start + limit - 6)
    if start:
        space = line.find(" ", start)
        start = space + 1 if 0 <= space < (match_start or start) else start
    if end < len(line):
        space = line.rfind(" ", start, end)
        end = space if space > start else end
    return ("..." if start else "") + line[start:end].strip(" ,;:") + ("..." if end < len(line) else "")


# ---------------------------------------------------------------- extraction

WEIGHT = {"must": 3, "role": 2, "intro": 1, "nice": 1}
KIND_ORDER = {"must": 0, "role": 1, "intro": 2, "nice": 3}


def extract(lines):
    """Skills the posting asks for, strongest first, each with the lines it appears on."""
    secs = sections(lines)
    found = {}
    for i, (line, sec) in enumerate(zip(lines, secs), start=1):
        if sec in ("about", "heading", "legal"):
            continue
        low = line.lower()
        for t in terms():
            m = t["_re"].search(line if "regex" in t else low)
            if not m or (t["_not"] and t["_not"].search(line)):
                continue
            f = found.setdefault(t["term"], {"term": t["term"], "cat": t["cat"], "score": 0, "lines": [], "kinds": set()})
            f["score"] += WEIGHT[sec]
            f["kinds"].add(sec)
            f["lines"].append({"line": i, "section": sec, "quote": quote(line, m.start())})
    result = []
    for f in found.values():
        kinds = f.pop("kinds")
        f["kind"] = min(kinds, key=lambda k: KIND_ORDER[k])
        f["mentions"] = len(f["lines"])
        best = min(f["lines"], key=lambda x: (KIND_ORDER[x["section"]], x["line"]))
        f["line"], f["quote"] = best["line"], best["quote"]
        result.append(f)
    result.sort(key=lambda f: (KIND_ORDER[f["kind"]], f["cat"] == "soft", -f["score"], f["line"]))
    return result


def grouped(skills, limit=5):
    """Skills that share their best line and kind, merged: 'Java, Go, Ruby (line 31)' instead of three entries."""
    groups = []
    for s in skills:
        g = next((g for g in groups if g["line"] == s["line"] and g["kind"] == s["kind"]), None)
        if g:
            g["terms"].append(s["term"])
            g["mentions"] = max(g["mentions"], s["mentions"])
        else:
            groups.append({"terms": [s["term"]], "kind": s["kind"], "line": s["line"], "quote": s["quote"],
                           "mentions": s["mentions"]})
    return groups[:limit]


def _names(terms, cap=4):
    if len(terms) > cap:
        return ", ".join(terms[:cap]) + f" and {len(terms) - cap} more"
    return terms[0] if len(terms) == 1 else ", ".join(terms[:-1]) + " and " + terms[-1]


FACTS = [
    ("pay", re.compile(r"\$\s?\d[\d,.]*\s*(k\b)?\s*(/\s?h(ou)?r|per hour|an hour|hourly|/\s?(yr|year)|per year|a year|"
                       r"/\s?mo(nth)?\b|per month|a month|monthly|annually|usd|-\s?\$|to \$|\+)|"
                       r"(pay|salary|compensation|rate|base|stipend)[^.]{0,60}\$\s?\d|"
                       r"\b\d{2,3}(?:,\d{3})+(?:\.\d+)?\s*USD\b", re.I)),
    ("graduation", re.compile(r"(graduat\w*|conferral|class of|degree by|enrolled)[^.]{0,80}?(20\d\d)|(20\d\d)[^.]{0,40}graduat",
                             re.I)),
    ("experience", re.compile(r"\b\d+\+?\s*(years?|yrs)\b(?!\s*(?:old|of age))", re.I)),
    ("work authorization", re.compile(r"(sponsorship|work authori[sz]ation|authori[sz]ed to work|work visa|"
                                      r"visa sponsorship|\b(?-i:CPT|OPT)\b|u\.?s\.? citizen|citizenship|"
                                      r"security clearance|\bitar\b|export control|(?:will not|won't|cannot|unable to|"
                                      r"does not|do not) (?:provide )?sponsor)", re.I)),
    ("interview process", re.compile(r"(interview (?:process|rounds?|loop|stages?)|\b\d+ rounds?\b|hackerrank|codesignal|"
                                     r"take[- ]home|coding challenge|online assessment)", re.I)),
    ("where you work", re.compile(r"(in[- ]office|hybrid|remote|on[- ]?site|days a week|relocat)", re.I)),
    ("start date", re.compile(r"(summer|fall|spring|winter) (of )?20\d\d|start date|\b\d{1,2}[- ]weeks?\b|weeks? long|"
                              r"starting (in|on)", re.I)),
]

GATES = re.compile(r"(top secret|ts/sci|secret clearance|security clearance|u\.?s\.? citizenship (?:is )?required|"
                   r"must be a u\.?s\.? citizen|active clearance|polygraph|(?:will not|won't|cannot|unable to|"
                   r"does not|do not) (?:provide )?sponsor)", re.I)


def gates(lines):
    """Eligibility requirements a student must know before prepping (clearance, citizenship)."""
    for i, line in enumerate(lines, start=1):
        m = GATES.search(line)
        if m:
            return {"line": i, "quote": quote(line, m.start())}
    return None


ASK_RECRUITER = {
    "interview process": "What the interview looks like (rounds, coding or behavioral, who you'll meet)",
    "pay": "Pay or hourly rate",
    "where you work": "Whether it's remote, hybrid or in office",
    "start date": "Start date and length of the internship",
}


PAY_WORD = re.compile(r"\b(pay|salary|compensation|hourly|rate|base|wage)\b|/\s?h(ou)?r|per hour|/\s?mo", re.I)
PERKS = re.compile(r"(\bhra\b|fertility|stipend|reimburs|401|perks?\b|discount|wellness|gym|commuter|\bpto\b|"
                   r"time off|holiday|vacation|sick)", re.I)


def facts(lines):
    secs = sections(lines)
    out = {}
    for name, rx in FACTS:
        for i, (line, sec) in enumerate(zip(lines, secs), start=1):
            in_text = name in ("pay", "where you work", "work authorization", "start date")
            if sec == "heading" or (sec in ("legal", "about") and not in_text) or \
                    (name in ("where you work", "start date") and PERKS.search(line)) or \
                    (name == "pay" and PERKS.search(line) and not PAY_WORD.search(line)):
                continue
            m = rx.search(line)
            if m:
                out[name] = {"line": i, "quote": quote(line, m.start())}
                break
    return out


STOP = set("""about above across after again against almost along already also although always among another
around because become becomes before being below between beyond build building built business businesses
company companies could create creating customers every everyone everything first great growth helps
including into itself people their there these thing things those through today together toward towards
under while which world would years where across within without work working works across ourselves
make makes making more most other others over same should since some such than that them then they this
those very what when will with your yours ours role team teams join help where whether every based
builds uses using accept sends owns moves helps provides offers powers enables small dollar dollars million
billion leading global largest best mission across experience experiences assistance hours gender opportunity
opportunities candidates candidate applicants employees employer position positions including include provide
qualified ability status location locations benefits salary salaries relocation range ranges shape right pay
compensation bonus equity health insurance eligible offer offers""".split())


CAPS_STOP = set("""The We Our You Your In At As This That These For With And Or If When Join Apply About Who What
Why How Summer Fall Spring Winter Intern Interns Internship Internships Program Software Engineer Engineering
Engineers Team Teams US USA United States Inc LLC Corp Corporation Company Monday Tuesday Wednesday Thursday Friday
January February March April May June July August September October November December Equal Opportunity
Bachelor Bachelors Master Masters PhD Degree University College Students Student Candidates Pay Benefits Location
Remote Hybrid Onsite New Today Year Years Day Days Job Jobs Role Roles Position Positions Minimum Preferred Basic
Qualifications Requirements Responsibilities Experience Skills Ability""".split())


def posting_keywords(lines, company, limit=6):
    """Product, platform or team names from the posting (Foundry, AWS, Payments Core), for "something specific"."""
    secs = sections(lines)
    picked = [l for l, s in zip(lines, secs) if s in ("intro", "about", "role") and not PERKS.search(l)
              and not FACTS[0][1].search(l)][:40]
    skip = {w.lower() for w in re.findall(r"[A-Za-z]+", company or "")}
    names = []
    for line in picked:
        for m in re.finditer(r"(?<![.!?]\s)(?<!^)\b([A-Z][A-Za-z0-9]+(?: [A-Z][A-Za-z0-9]+){0,2})", line):
            words = [w for w in m.group(1).split() if w not in CAPS_STOP and w.lower() not in skip]
            if words and len(" ".join(words)) > 2:
                names.append(" ".join(words))
    return [w for w, _ in Counter(names).most_common(limit)]


# ---------------------------------------------------------------- brief

def brief_markdown(rec, top_n=5):
    p, skills, fx = rec["posting"], rec["skills"], rec["facts"]
    company, title = p["company"], p["title"]
    groups = grouped([s for s in skills if s["kind"] != "nice"], top_n)
    in_lead = {t for g in groups for t in g["terms"]}
    lead = [s for s in skills if s["term"] in in_lead]
    rest = [s for s in skills if s["term"] not in in_lead]
    md = [f"# {title} at {company}: interview brief", ""]
    md.append(f"Interview: {rec.get('when') or 'time not set yet'}")
    if p.get("location"):
        md.append(f"Location: {p['location']}")
    md.append(f"Posting: {p['source_url'] or 'pasted by the student'} (read {p['fetched_at'][:10]} from {p['board']})")
    if rec.get("closed_at"):
        md.append(f"Note: the posting was closed on {rec['closed_at'][:10]}; this brief uses the copy saved earlier.")
    gate = gates(p["lines"])
    if gate:
        md += ["", f"Check first: \"{gate['quote']}\" (line {gate['line']})"]
    md += ["", "Every point below cites a numbered line of the posting (full text at the end).", "",
           "## What they ask for most", ""]
    if groups:
        for n, g in enumerate(groups, 1):
            label = {"must": "requirement", "role": "part of the job", "intro": "mentioned"}[g["kind"]]
            times = f", {g['mentions']} mentions" if g["mentions"] > 1 and len(g["terms"]) == 1 else ""
            md.append(f"{n}. {_names(g['terms'])} ({label}{times}): \"{g['quote']}\" (line {g['line']})")
    else:
        md.append("No specific tools or skills are named in this posting. Prepare general examples instead.")
    if rest:
        md += ["", "## Also mentioned", ""]
        md += [f"- {s['term']}{' (nice to have)' if s['kind'] == 'nice' else ''}: line {s['line']}" for s in rest[:10]]
    md += ["", "## Practical details", ""]
    for name in ("pay", "where you work", "start date", "graduation", "experience", "work authorization", "interview process"):
        if name in fx:
            md.append(f"- {name.capitalize()}: \"{fx[name]['quote']}\" (line {fx[name]['line']})")
    missing = [ASK_RECRUITER[k] for k in ASK_RECRUITER if k not in fx]
    if missing:
        md += ["", "## Not in the posting: ask your recruiter", ""] + [f"- {m}" for m in missing]
    md += ["", "## Questions to practice", ""]
    for n, q in enumerate(plan_questions(rec)[:4], 1):
        md.append(f"{n}. {q['question']}" + (f" (because of line {q['line']})" if q.get("line") else ""))
    keywords = rec.get("keywords") or []
    lead = [s for s in lead if s["cat"] != "soft"] or lead
    md += ["", "## A 30-second pitch outline", ""]
    md.append(f"- Who you are, plus one project that shows {lead[0]['term'] if lead else 'what the role needs'}")
    if len(lead) > 1:
        md.append(f"- Where you used {lead[1]['term']}" + (f" or {lead[2]['term']}" if len(lead) > 2 else ""))
    md.append(f"- Why {company}" + (f": mention \"{keywords[0]}\" from their posting" if keywords else ""))
    md += ["", "## The posting, numbered", ""]
    md += [f"{i}. {line}" for i, line in enumerate(p["lines"], 1)]
    return "\n".join(md) + "\n"


def brief_texts(rec):
    """The texts that go with the brief: what to expect, the top ask in the posting's own words, the rest."""
    p = rec["posting"]
    first = (f"{p['company']} interview {rec['when']}. Your prep brief is ready." if rec.get("when")
             else f"{p['company']}, {p['title']}: your prep brief is ready.")
    hard = [s for s in rec["skills"] if s["kind"] != "nice" and s["cat"] != "soft"]
    groups = grouped(hard or [s for s in rec["skills"] if s["kind"] != "nice"], 4)
    if not groups:
        return [first, "The posting doesn't name specific tools, so we'll practice general questions."]
    top = groups[0]
    texts = [first, f"Top of their list: {_names(top['terms'], 3)}. In their words: \"{quote(top['quote'], None, 90)}\""]
    if len(groups) > 1:
        texts.append("Then " + "; ".join(_names(g["terms"], 3) for g in groups[1:]) + ".")
    if len([s for s in rec["skills"] if s["kind"] != "nice"]) < 3:
        texts.append("Heads up: this posting names very few specific skills, so prep your projects and the why-us story.")
    gate = gates(p["lines"])
    if gate:
        texts.append(f"Check this first: \"{quote(gate['quote'], None, 110)}\" (line {gate['line']})")
    texts.append("The brief quotes the posting for every point, with line numbers so you can check it.")
    return texts


def headline(rec):
    hard = [s for s in rec["skills"] if s["kind"] != "nice" and s["cat"] != "soft"]
    groups = grouped(hard or [s for s in rec["skills"] if s["kind"] != "nice"], 3)
    if not groups:
        return "The posting doesn't name specific tools, so we'll practice general questions."
    first = groups[0]
    if len(first["terms"]) == 1 and first["mentions"] > 1:
        where = f" ({first['mentions']} mentions, line {first['line']})"
    else:
        where = f" (line {first['line']})"
    others = ", then ".join(_names(g["terms"], 3) for g in groups[1:])
    return f"Their posting leans on {_names(first['terms'], 3)}{where}" + (f", then {others}." if others else ".")


# ---------------------------------------------------------------- questions

def _fill(text, company, term=None):
    return text.replace("{company}", company or "the company").replace("{term}", term or "this")


def _resolve_points(points, rec, term=None):
    company = rec["posting"]["company"]
    top = next((s for s in rec["skills"] if s["kind"] != "nice"), None)
    out = []
    for p in points:
        if (p.get("company") and not company) or (p.get("posting_keywords") and not rec.get("keywords")):
            continue
        phrases = [x.lower() for x in p.get("any", [])]
        if p.get("term") and term:
            info = term_info(term)
            phrases += [term.lower()] + [a.lower() for a in (info or {}).get("aliases", [])]
        if p.get("company") or p.get("company_ok"):
            phrases.append(company.lower())
        if p.get("posting_keywords"):
            phrases += rec.get("keywords") or []
        if p.get("top_term") and top:
            info = term_info(top["term"])
            phrases += [top["term"].lower()] + [a.lower() for a in (info or {}).get("aliases", [])]
        label = _fill(p["label"], company, term)
        if p.get("top_term") and top:
            label = f"mentions {top['term']} (the posting's top skill)"
        cues = [c.lower() for c in p.get("cues", [])] + [x for x in phrases if len(x) >= 6]
        out.append({"label": label, "phrases": sorted(set(phrases)), "number": bool(p.get("number")),
                    "cite": _cite(rec, cues)})
    return out


def _cite(rec, cues):
    """The first posting line (outside the about-us and benefits text) that backs up a point."""
    if not cues:
        return None
    lines = rec["posting"]["lines"]
    for i, (line, sec) in enumerate(zip(lines, sections(lines)), start=1):
        if sec in ("about", "heading", "legal"):
            continue
        low = line.lower()
        for c in cues:
            m = _phrase_re(c).search(low)
            if m:
                return {"line": i, "quote": quote(line, m.start())}
    return None


def plan_questions(rec):
    """Every question available for this interview, in the order they should be asked."""
    company = rec["posting"]["company"]
    bank = questions()
    tech, soft, projects = [], [], []

    def item(e, s):
        pts = (bank["star_points"] if e.get("star") else []) + e["points"]
        return {"id": e["id"], "question": _fill(e["q"], company, s["term"]), "term": s["term"],
                "line": s["line"], "quote": s["quote"], "points": _resolve_points(pts, rec, s["term"])}

    for s in [s for s in rec["skills"] if s["kind"] != "nice"][:6]:
        project = {"id": f"project-{slug(s['term'])}", **bank["generic_tech"]}
        entries = bank["terms"].get(s["term"])
        if s["cat"] == "soft":
            soft += [item(e, s) for e in entries or []]
        elif entries:
            tech += [item(e, s) for e in entries]
            projects.append(item(project, s))  # a second, experience-based question on the same skill
        else:
            tech.append(item(project, s))
    general = [{"id": e["id"], "question": _fill(e["q"], company), "term": None, "line": None, "quote": None,
                "points": _resolve_points(e["points"], rec)} for e in bank["company"]]
    order, pools = [], [tech, general[:1], tech, soft, general[1:], tech, projects, soft, projects]
    while any(pools):
        for pool in pools:
            if pool:
                order.append(pool.pop(0))
    seen, unique = set(), []
    for q in order:
        if q["id"] not in seen:
            seen.add(q["id"])
            unique.append(q)
    return unique


TONES = {
    "chill": {
        "opener": "Hi, it's Cubbie with your {company} mock interview. No pressure, this is just practice.",
        "ask": "Here's your question. {q}",
        "close": "Take a second to think. When we hang up, text me your answer as a voice note. About a minute is perfect.",
    },
    "tough": {
        "opener": "This is your {company} mock interview. Today I'll be a tough interviewer, so be specific.",
        "ask": "{q} Give me one real example.",
        "close": "When we hang up, send your answer as a voice note. Aim for 60 to 90 seconds and get to the point.",
    },
}


def next_question(rec, tone="chill", question_id=None, term=None):
    plan = plan_questions(rec)
    asked = rec.get("asked", [])
    if question_id:
        pick = next((q for q in plan if q["id"] == question_id), None)
    elif term:
        pick = next((q for q in plan if q["term"] and q["term"].lower() == term.lower() and q["id"] not in asked), None)
    else:
        pick = next((q for q in plan if q["id"] not in asked), None)
    if pick is None:
        return None
    t = TONES.get(tone, TONES["chill"])
    company = rec["posting"]["company"]
    why = f"The posting says \"{pick['quote']}\" (line {pick['line']})." if pick.get("line") else None
    pick = dict(pick, tone=tone, why=why)
    pick["call_script"] = " ".join([_fill(t["opener"], company), t["ask"].replace("{q}", pick["question"]), t["close"]])
    pick["text_version"] = f"Mock question for {company}:\n{pick['question']}\nAnswer with a voice note or a text whenever you're ready."
    return pick


# ---------------------------------------------------------------- scoring

FILLERS = ["um", "uh", "you know", "kind of", "sort of", "basically", "literally", "i mean"]
COURSE_CODE = re.compile(r"\b[A-Z]{2,4} ?\d{3}[A-Z]?\b")
NUMBER = re.compile(r"(?<![a-z0-9])\d|\b(percent|half|twice|double|triple|hundred|thousand|million|dozen)\b")


def _phrase_re(p):
    """Two-letter phrases match whole words; 3-4 letters allow s/es/ed ("tests"); longer ones are stems ("idempoten")."""
    if len(p) <= 2:
        tail = r"(?![a-z0-9+#])"
    elif len(p) <= 4:
        tail = r"(?:s|es|ed)?(?![a-z0-9+#])"
    else:
        tail = ""
    return re.compile(rf"(?<![a-z0-9]){re.escape(p)}{tail}")


NEGATION = re.compile(r"(don't|do not|didn't|did not|never|not sure|no idea|not familiar with|unfamiliar with|"
                      r"no experience (?:with|in)|haven't|have not|not really)"
                      r"(?: \w+){0,3} $")


def _said(phrase, text):
    """True when the answer uses the phrase, not counting "I don't know X" or "never used X"."""
    for m in _phrase_re(phrase).finditer(text):
        if not NEGATION.search(text[max(0, m.start() - 40):m.start()]):
            return True
    return False


def score_answer(question, answer):
    no_codes = " ".join(COURSE_CODE.sub(" ", answer or "").lower().split())  # "CSE 340" is a class, not a result
    text = " ".join((answer or "").lower().replace("’", "'").split())
    results = []
    for p in question["points"]:
        hit = next((ph for ph in p["phrases"] if _said(ph, text)), None)
        if not hit and p.get("number") and NUMBER.search(no_codes):
            hit = NUMBER.search(no_codes).group(0)
        results.append({"point": p["label"], "covered": bool(hit), "evidence": hit, "cite": p.get("cite")})
    words = re.findall(r"[a-z0-9']+", text)
    fillers = {f: len(re.findall(rf"(?<![a-z]){re.escape(f)}(?![a-z])", text)) for f in FILLERS}
    fillers = {k: v for k, v in fillers.items() if v}
    i_count = sum(1 for w in words if w in ("i", "i'm", "i've", "i'd", "my"))
    we_count = sum(1 for w in words if w in ("we", "we're", "we've", "our"))
    covered = sum(r["covered"] for r in results)
    total = len(results) or 1
    ratio = covered / total
    verdict = "strong" if ratio >= 0.75 else "solid" if ratio >= 0.5 else "needs work"
    hedged = bool(re.search(r"\b(not sure|no idea|i don't know|i guess|maybe)\b", text))
    if verdict == "strong" and (len(re.findall(r"[a-z0-9']+", text)) < 50 or hedged):
        verdict = "solid"  # key words alone, or a hedge, aren't a strong answer
    tips = []
    if len(words) < 40:
        tips.append("Too short. Aim for about a minute, around 120 to 180 words.")
    if re.search(r"\b(not sure|no idea|i guess)\b", text):
        tips.append("Drop the hedging (\"not sure\", \"I guess\"); say what you'd do and why.")
    elif len(words) > 320:
        tips.append("Long. Tighten it to about 90 seconds.")
    if we_count >= 4 and we_count > 2 * max(i_count, 1):
        tips.append(f"You said \"we\" {we_count} times and \"I\" {i_count}. Say what you did.")
    filler_total = sum(fillers.values())
    if filler_total >= 5:
        top = ", ".join(f"\"{k}\" x{v}" for k, v in sorted(fillers.items(), key=lambda kv: -kv[1])[:2])
        tips.append(f"{filler_total} filler words ({top}). A short pause sounds better.")
    missed = [r["point"] for r in results if not r["covered"]]
    return {
        "question_id": question["id"], "covered": covered, "total": len(results), "verdict": verdict,
        "points": results, "missed": missed, "words": len(words), "fillers": fillers, "tips": tips,
        "cite": f"line {question['line']}" if question.get("line") else None, "term": question.get("term"),
    }


# ---------------------------------------------------------------- invite emails

REJECT = re.compile(r"(we (?:will|are|have) (?:not|decided not)[^.]{0,30}(?:moving forward|proceed|pursue)|"
                    r"decided not to (?:move|proceed)|(?:won'?t|will not|are not) (?:be )?(?:moving forward|proceeding|"
                    r"extending)|not (?:be )?moving forward with your|(?:decided|chosen) to (?:pursue|move forward with) "
                    r"other|moving forward with (?:other|another) (?:candidates?|applicants?)|unable to offer|"
                    r"extending an offer|filled the (?:role|position)|not able to move you forward|go with other "
                    r"(?:candidates|applicants)|won'?t be able to invite you|go in a different direction|"
                    r"position has been filled|we regret to inform|"
                    r"not been selected|were not selected)", re.I)
RESCHEDULE = re.compile(r"(reschedul|original time|move your interview|new time|different time)", re.I)
ASSESS = re.compile(r"(online assessment|coding assessment|hackerrank|codesignal|codility|hirevue|take[- ]home|"
                    r"technical assessment|\bOA\b)", re.I)
SCHEDULE = re.compile(r"(calendly\.com|goodtime\.io|schedul(e|ing) (link|your|an interview|a time)|select (a|your) time|"
                      r"pick a time|share your availability|your availability|book a time|choose a time)", re.I)
CONFIRMED = re.compile(r"(confirmed|is scheduled|has been scheduled|calendar invit|see you (on|at)|zoom\.us/|"
                       r"meet\.google\.com|teams\.microsoft\.com|join the (call|meeting))", re.I)
INTERVIEW = re.compile(r"(interview|phone screen|technical screen|onsite|on-site|super ?day|final round|next round|"
                       r"recruiter (call|chat))", re.I)
URL = re.compile(r"https?://[^\s<>\"')\]]+")
NOT_COMPANY = {"gmail", "outlook", "hotmail", "yahoo", "icloud", "greenhouse", "greenhouse-mail", "lever", "hire",
               "ashbyhq", "goodtime", "myworkday", "workday", "smartrecruiters", "calendly", "handshake",
               "joinhandshake", "linkedin", "indeed", "asu", "edu", "mail", "notifications", "us"}


def classify_email(text, parse_link):
    """Is this an interview invite, a request to schedule, an assessment, a rejection, or something else?"""
    signals = []
    if (REJECT.search(text) or (re.search(r"\bunfortunately\b", text, re.I) and not CONFIRMED.search(text)
                                and not SCHEDULE.search(text))) and not RESCHEDULE.search(text):
        category = "rejection"
        signals.append("rejection wording")
    elif ASSESS.search(text) and not CONFIRMED.search(text):
        category = "assessment"
        signals.append("online assessment")
    elif INTERVIEW.search(text) and CONFIRMED.search(text):
        category = "interview_confirmed"
        signals.append("interview with a confirmed time or meeting link")
    elif INTERVIEW.search(text) and SCHEDULE.search(text):
        category = "schedule_request"
        signals.append("asks the student to pick a time")
    elif INTERVIEW.search(text):
        category = "interview"
        signals.append("mentions an interview")
    else:
        category = "other"
    job_links = []
    for url in dict.fromkeys(URL.findall(text)):
        url = url.rstrip(".,;")
        try:
            link = parse_link(url)
        except Exception:
            continue
        if link["board"] != "webpage" or re.search(r"/(jobs?|careers?|positions?|openings?)/", url, re.I):
            job_links.append({"url": url, "board": link["board"]})
    sender = re.search(r"^from:.*?@([a-z0-9.-]+)", text, re.I | re.M)
    company_hint = None
    if sender:
        parts = [x for x in sender.group(1).lower().split(".")
                 if x not in ("com", "io", "co", "org", "net", "ai", "uk", "us", "ca", "in", "de", "edu", "gov", "jobs", "app")]
        cand = parts[-1] if parts else None
        if cand and cand not in NOT_COMPANY:
            company_hint = cand.capitalize()
    nxt = {
        "rejection": "Not an invite. Don't start prep; be kind if the student brings it up.",
        "assessment": "An online assessment, not an interview. Offer practice for the skills in the posting instead of a mock call.",
        "interview_confirmed": "An interview with a time. Fetch the posting and offer the brief.",
        "schedule_request": "They need to pick a time first. Remind the student to reply, then fetch the posting.",
        "interview": "Probably an invite. Confirm the company, role and time with the student, then fetch the posting.",
        "other": "Not an interview email.",
    }[category]
    return {"category": category, "signals": signals, "job_links": job_links, "company_hint": company_hint, "next": nxt}
