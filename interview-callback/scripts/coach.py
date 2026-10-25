"""Turn numbered posting lines into cited must-haves, a prep brief, mock questions and an answer scorecard.

Everything here is deterministic: Cubbie phrases the results, but every skill, fact and score traces back to a
posting line or to a phrase in the student's answer.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Callable

ASSETS = Path(__file__).resolve().parent.parent / "assets"

# ---------------------------------------------------------------- dictionaries

_TERMS = None
_QUESTIONS = None


def terms() -> list[dict]:
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


def questions() -> dict:
    global _QUESTIONS
    if _QUESTIONS is None:
        _QUESTIONS = json.loads((ASSETS / "questions.json").read_text(encoding="utf-8"))
    return _QUESTIONS


def term_info(name: str) -> dict | None:
    return next((t for t in terms() if t["term"] == name), None)


def slug(text: str | None, n: int = 40) -> str:
    text = (text or "").lower().replace("+", " plus ").replace("#", " sharp ")  # C, C++ and C# stay distinct
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:n].strip("-") or "x"


SPOKEN = re.compile(r"[A-Za-z0-9][\w'&.!-]*(?: (?:of|and|the|for|&|[A-Z0-9][\w'&.!-]*)){0,4}")
CORP_SUFFIX = re.compile(r",?\s+(?:inc|llc|ltd|corp|corporation|co|plc|gmbh)\.?$", re.I)
# "Text Your Password To 5550100" is Title Case but an instruction: an imperative aimed at someone, or a long number,
# never reads as a name ("Saguaro Pay" and "Email Marketing Intern" stay).
IMPERATIVE = re.compile(r"\b(?:text|call|email|e-mail|send|visit|click|reply|pay|share|ignore|wire|tell|dial|"
                        r"download)\s+(?:your|me|us|the|this|that|now|to|stop|\d)\b|\d{3}", re.I)
NO_COMPANY = "the company"


def spoken_company(name: str | None) -> str:
    """The company as Cubbie may say it on a call: a plain name ("Bank of America", "T-Mobile"), else "the company"."""
    name = CORP_SUFFIX.sub("", re.sub(r"\s+", " ", name or "").strip()).strip(" ,")
    return name if len(name) <= 40 and SPOKEN.fullmatch(name) and not IMPERATIVE.search(name) else NO_COMPANY


TITLE_SMALL = {"and", "of", "for", "the", "in", "to", "with", "at", "on", "a", "an", "or", "de"}


def spoken_title(title: str | None) -> str:
    """The job title as Cubbie may text it: a plain title, else "this role" (titles come from the posting)."""
    title = re.sub(r"\s+", " ", title or "").strip()
    lower = [w for w in re.findall(r"\b[a-z][\w']*", title) if w not in TITLE_SMALL]
    if not title or len(title) > 140 or len(lower) > 1 or IMPERATIVE.search(re.sub(r"\b(19|20)\d\d\b", "", title)) \
            or re.search(r"\w@\w|https?:|www\.|\.(?:com|net|org|io|example)\b|\d{5}", title):
        return "this role"
    return title


# ---------------------------------------------------------------- sections of a posting

NICE_HDR = re.compile(r"(prefer|nice[- ]to[- ]have|bonus|\bplus\b|desired|extra credit|not required|good to have|"
                      r"even better|better if|stand out)")
MUST_HDR = re.compile(r"(requirement|qualification|must[- ]have|you have|you'?ll need|you will need|what you need|"
                      r"looking for|what we look for|who you are|about you|minimum|basic|required|skills|"
                      r"you bring|you'?ll bring|ideal candidate|you might be a fit|you may be a fit|\brequire\b|"
                      r"our person if|you'?re our person|what we need to see)")
# "Potential opportunity areas include:" / "Where Your Career Could Take You": a list of job titles, not duties
TITLES_HDR = re.compile(r"(opportunity areas|career could take you|where you could (?:go|work)|roles? you could|"
                        r"potential (?:roles|teams|paths))")
JOB_TITLE = re.compile(r"^- (?:[A-Z][\w&+/.-]*\s){0,6}(?:Engineer|Scientist|Developer|Analyst|Architect)"
                       r"(?:/(?:Engineer|Scientist|Developer|Analyst))?s?$")
ROLE_HDR = re.compile(r"(responsib|what you'?ll (?:actually )?(?:do|be doing)|what you will do|the role|the job|"
                      r"about the (team|role|job|position)|"
                      r"your impact|day[- ]to[- ]day|in this role|you will|the opportunity|what you'?ll work on|"
                      r"job description|^description|overview|summary|the team|projects|"
                      r"about (the|this) (role|internship|program|position|job))")
ABOUT_HDR = re.compile(r"(^about (?!(the|this) (role|team|job|position|opportunity|internship|program|intern|"
                       r"co-?op))|who we are|^our (mission|story|company|values|culture)|"
                       r"benefits|perks|^pay\b|pay and benefits|compensation|salary|"
                       r"equal (employment )?opportunity|eeo|"
                       r"diversity|accommodation|our commitment|why join|life at|office expectations|hybrid work|"
                       r"privacy|disclaimer|what we offer|apply now|unleash your)")
# Workday's header block (Date Posted:, Country:, Security Clearance Type:) is metadata, not a requirements list
META_HDR = re.compile(r"(date posted|^country|^location|role type|citizen|clearance|immigration|relocation eligib)")
BOILERPLATE = re.compile(r"(equal (employment )?opportunit|without regard to|regardless of|race, (color|religion)|"
                         r"gender identity|sexual orientation|national origin|protected veteran|veteran status|"
                         r"disabilit(y|ies)|reasonable accommodation|e-?verify|pay transparency|"
                         r"benefits (include|package)|"
                         r"401\(k\)|paid time off|medical, dental|background check|drug (test|screen)|"
                         r"applicants (will|must|are)|we are committed to|affirmative action|privacy (notice|policy)|"
                         r"accommodation|paid (?:time off|holidays|sick)|sick (?:leave|time)|\bpto\b|vacation days|"
                         r"parental leave)", re.I)
PITCH = re.compile(r"^(?:what if|imagine|whether you|are you ready|ready to|we invite you|bring your|shape the future|"
                   r"at [A-Z][\w&.-]*(?: [A-Z][\w&.-]*){0,2}, (?:you|we|our)|from [^,]{3,80} to [^,]{3,80}, you)|[?!]$",
                   re.I)
# Third-person prose about the company in a paragraph (never a "- " duty bullet): "Intel brings them together",
# "Unlike many companies...", "What makes Intel unique...", "Our Platform team is...", "...shape what is next".
COMPANY_PROSE = re.compile(r"^(?:what makes [^,.]{2,40} (?:unique|different|special)|unlike (?:many|most|other|"
                           r"traditional)\b|(?-i:[A-Z][\w&.-]*(?: [A-Z][\w&.-]*){0,2}) (?:brings|is (?:committed|proud|"
                           r"known|home)|was founded|empowers|believes)\b|our (?:[\w&-]+ ){0,4}teams? (?:is|are)\b"
                           r"(?! (?:looking|seeking|hiring)))|\b(?:shape|define|redefine) (?:the future|"
                           r"what(?:'s| is) next)\b", re.I)
INLINE_NICE = re.compile(r"\b(preferred|a plus|is a plus|are a plus|nice to have|bonus|ideally|not required)\b", re.I)


def classify_heading(line: str) -> str | None:
    """Section for a heading line, or None when the line is ordinary content."""
    if ":" in line and line.split(":", 1)[1].strip():
        return None
    if line.rstrip().endswith(("...", "\u2026")) and len(line.split()) <= 6:
        line = line.rstrip().rstrip(".\u2026 ") + ":"  # "You're Our Person If..." is a heading
    text = line.strip().rstrip(":").strip().lower()
    if line.startswith("- ") or len(text) > 70 or (text.endswith(".") and not line.endswith(":")):
        return None
    if TITLES_HDR.search(text) and len(text.split()) <= 7 and not text.endswith(("!", "?")):
        return "titles"
    if not line.endswith(":") and (len(text.split()) > 5 or any(
            t["_re"].search(line if "regex" in t else text) for t in terms())):
        return None  # "You will build APIs in Python" is content, not a heading
    if META_HDR.search(text):
        return "keep"
    for name, rx in (("about", ABOUT_HDR), ("nice", NICE_HDR), ("must", MUST_HDR), ("role", ROLE_HDR)):
        if rx.search(text):
            return name
    return "keep" if line.endswith(":") else None


_SECTIONS = {}


def sections(lines: list[str]) -> list[str]:
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
        elif current in ("role", "must", "titles") and JOB_TITLE.match(line):
            out.append("about")  # "- Cloud Software Development Engineer" names a job, not a skill to prep
        elif current == "titles" or (current in ("role", "must") and (
                PITCH.search(line) or (not line.startswith("- ") and COMPANY_PROSE.search(line)))):
            out.append("intro")  # a recruiting pitch inside the job description isn't a duty or a requirement
        else:
            out.append("nice" if current in ("must", "role", "intro") and INLINE_NICE.search(line) else current)
    return out


def quote(line: str, match_start: int | None = None, limit: int = 140) -> str:
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


def extract(lines: list[str], company: str | None = None) -> list[dict]:
    """Skills the posting asks for, strongest first, each with the lines it appears on."""
    secs = sections(lines)
    own = {w for w in re.findall(r"[a-z0-9]+", (company or "").lower()) if len(w) > 2}
    found = {}
    for i, (line, sec) in enumerate(zip(lines, secs), start=1):
        if sec in ("about", "heading", "legal"):
            continue
        low = line.lower()
        for t in terms():
            m = t["_re"].search(line if "regex" in t else low)
            if not m or (t["_not"] and t["_not"].search(line)) or m.group(0).lower() in own:
                continue
            f = found.setdefault(t["term"], {"term": t["term"], "cat": t["cat"], "score": 0, "lines": [],
                                             "kinds": set()})
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


ALTERNATIVES = re.compile(r"\bor\b|\bone of\b|\bany of\b|\beither\b", re.I)


def grouped(skills: list[dict], limit: int = 5) -> list[dict]:
    """Skills that share their best line and kind, merged: 'Java, Go, Ruby (line 31)' instead of three entries.
    A group whose line offers them as a choice ("Java, Go or Python") is marked `alternatives`."""
    groups = []
    for s in skills:
        g = next((g for g in groups if g["line"] == s["line"] and g["kind"] == s["kind"]), None)
        if g:
            g["terms"].append(s["term"])
            g["cats"].add(s["cat"])
            g["mentions"] = max(g["mentions"], s["mentions"])
        else:
            groups.append({"terms": [s["term"]], "cats": {s["cat"]}, "kind": s["kind"], "line": s["line"],
                           "quote": s["quote"], "mentions": s["mentions"]})
    for g in groups:
        g["alternatives"] = len(g["terms"]) > 1 and len(g.pop("cats")) == 1 and bool(ALTERNATIVES.search(g["quote"]))
    return groups[:limit]


def _names(terms, cap=4, joiner="and"):
    if len(terms) > cap:
        return ", ".join(terms[:cap]) + f" and {len(terms) - cap} more"
    return terms[0] if len(terms) == 1 else ", ".join(terms[:-1]) + f" {joiner} " + terms[-1]


def _group_names(g, cap=3):
    return _names(g["terms"], cap, "or" if g.get("alternatives") else "and")


def backed(skills: list[dict]) -> list[dict]:
    """Hard skills a requirement or a duty asks for (not just the company pitch or a nice-to-have)."""
    return [s for s in skills if s["kind"] in ("must", "role") and s["cat"] != "soft"]


def language_options(skills: list[dict]) -> list[str]:
    """Languages the posting offers as a choice ("Java, Go or Python"): the student picks one to interview in."""
    langs = [s for s in skills if s["cat"] == "language" and s["kind"] != "nice"]
    for g in grouped(langs, 10):
        if g["alternatives"]:
            return g["terms"]
    return []


# Every way a posting says it won't sponsor a visa (Intel, Honeywell, Amex wording included).
NO_SPONSOR = (r"(?:will not|won't|cannot|unable to|does not|do not) (?:provide |offer |pursue )?(?:visa )?sponsor|"
              r"(?:not eligible for|without (?:the need for |requiring )?)[^.;]{0,40}sponsorship|"
              r"sponsorship is not (?:available|offered|provided)|unable to consider[^.;]{0,60}sponsorship|"
              r"(?:now|currently) or in the future[^.;]{0,40}sponsorship|sponsorship[^.;]{0,40}now or in the future")
EEO = re.compile(r"(equal (employment )?opportunit|without regard to|regardless of|race, (color|religion)|"
                 r"national origin|protected veteran|affirmative action)", re.I)
WHERE_STRONG = re.compile(r"(in[- ]office|hybrid|remote|on[- ]?site|days a week)", re.I)

FACTS = [
    ("pay", re.compile(r"\$\s?\d[\d,.]*\s*(k\b)?\s*(/\s?h(ou)?r|per hour|an hour|hourly|/\s?(yr|year)|per year|a year|"
                       r"/\s?mo(nth)?\b|per month|a month|monthly|annually|usd|-\s?\$|to \$|\+)|"
                       r"(pay|salary|compensation|rate|base|stipend)[^.]{0,60}\$\s?\d|"
                       r"\b\d{2,3}(?:,\d{3})+(?:\.\d+)?\s*USD\b|\b\d[\d,.]*\s*USD\s*[-–]\s*\$?\d[\d,.]*\s*USD\b",
                       re.I)),
    ("graduation", re.compile(r"(graduat\w*|conferral|class of|degree by|enrolled)[^.]{0,80}?(20\d\d)|"
                              r"(20\d\d)[^.]{0,40}graduat", re.I)),
    ("experience", re.compile(r"\b\d+\+?\s*(years?|yrs)\b(?!\s*(?:old|of age))", re.I)),
    ("work authorization", re.compile(r"(" + NO_SPONSOR + r"|sponsorship|work authori[sz]ation|authori[sz]ed to work|"
                                      r"work visa|\b(?-i:CPT|OPT)\b|u\.?s\.? citizen|citizenship[^.]{0,40}"
                                      r"(?:required|must)|(?:required|must)[^.]{0,40}citizenship|"
                                      r"security clearance|\bitar\b|export control)", re.I)),
    ("interview process", re.compile(r"(interview (?:process|rounds?|loop|stages?)|\b\d+ rounds?\b|hackerrank|"
                                     r"codesignal|take[- ]home|coding challenge|online assessment)", re.I)),
    ("where you work", re.compile(r"(in[- ]office|hybrid|remote|on[- ]?site|days a week|relocat)", re.I)),
    ("start date", re.compile(r"(summer|fall|spring|winter) (of )?20\d\d|start date|\b\d{1,2}[- ]weeks?\b|weeks? long|"
                              r"starting (in|on)", re.I)),
]

GATES = re.compile(r"(top secret|ts/sci|secret clearance|security clearance|u\.?s\.? citizenship (?:is )?required|"
                   r"must be a u\.?s\.? citizen|active clearance|polygraph|" + NO_SPONSOR + ")", re.I)


def gates(lines: list[str]) -> dict | None:
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


def facts(lines: list[str], prefer: Callable[[str], bool] | None = None) -> dict:
    """Pay, location, dates and eligibility lines. `prefer(line)` picks the pay line for the student's place."""
    secs = sections(lines)
    out = {}
    for name, rx in FACTS:
        order = list(enumerate(zip(lines, secs), start=1))
        if name == "pay" and prefer:
            order.sort(key=lambda x: not prefer(x[1][0]))
        if name == "where you work":  # "on-site, 3 days a week" beats "relocation assistance may be available"
            order.sort(key=lambda x: not WHERE_STRONG.search(x[1][0]))
        for i, (line, sec) in order:
            in_text = name in ("pay", "where you work", "work authorization", "start date")
            if sec == "heading" or (sec in ("legal", "about") and not in_text) or \
                    (name in ("where you work", "start date") and PERKS.search(line)) or \
                    (name == "pay" and PERKS.search(line) and not PAY_WORD.search(line)) or \
                    (name == "start date" and re.search(r"clearance|citizen", line, re.I)) or \
                    (name == "work authorization" and EEO.search(line) and
                     not re.search(r"sponsor|authori[sz]ed to work|work authori", line, re.I)):
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


NOT_NAMES = set("""LinkedIn YouTube Facebook Instagram Twitter Glassdoor Indeed TikTok FBI Strong Enrolled Category
Start End Applications Discover Tech Specific Information Quality Unit Citizen Total Rewards World Changing Product
Development Build Backend Infrastructure Operate Write Give Data Employer Work Manage Learn Hourly Fortune Modeling
Space Plan Develop Participate Collaborate Debug Contribute Design Agile Services Computer Science USD SDE SWE FBI
Technologies Alliance""".split())
PLACE_WORDS = re.compile(r"\b(alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|"
                         r"hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|"
                         r"michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|"
                         r"new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|"
                         r"rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|"
                         r"west virginia|wisconsin|wyoming|america|seattle|san francisco|bay area|chicago|austin|"
                         r"phoenix|tempe|chandler|scottsdale|boston|los angeles|denver|atlanta|dallas|san jose|"
                         r"mountain view|palo alto|redmond|portland|miami|hub|rd|bldg|street|ave)\b", re.I)
SCAM = re.compile(r"scam|fraud|phishing|complaint|impersonat", re.I)
ROLE_PLURAL = re.compile(r"(Managers|Strategists|Engineers|Designers|Scientists|Analysts|Recruiters|Leaders)$|"
                         r"Privacy|Statement|Policy|Department|Adjudication|Equal|Accommodation|Benefit|Candidate|"
                         r"Alliance")
# Recruiting programs ("Futureforce Recruiting", "Early Careers") aren't something to say in a why-us answer
RECRUITING = re.compile(r"\b(Recruiting|Recruitment|Careers?|Talent|Campus|University|Early (?:Career|Talent)|"
                        r"Hiring|Internship Program)\b")


def posting_keywords(lines: list[str], company: str | None, limit: int = 6, title: str = "") -> list[str]:
    """Product, platform or team names from the posting (Foundry, Payments Core, MAGNet), for "something specific".

    Only sentences about the company or the role count, not perks, pay or office lists. A name qualifies when the
    posting repeats it, it has several capitalized words, or it is written like a product (FigJam, MAGNet).
    """
    secs = sections(lines)
    picked = [l for l, s in zip(lines, secs) if s in ("intro", "about", "role") and not PERKS.search(l)
              and not FACTS[0][1].search(l) and len(l.split()) >= 7 and not SCAM.search(l)
              and not (l.count(",") >= 4 and len(re.findall(r",\s*[A-Z]", l)) >= 3)][:30]
    skip = {w.lower() for w in re.findall(r"[A-Za-z]+", f"{company or ''} {title or ''}")}
    names = Counter()
    for line in picked:
        body = re.sub(r"^\W+\s*", "", line)  # the first word of a bullet is capitalized anyway
        for m in re.finditer(r"(?<![.!?:]\s)(?<!^)(?<![\w-])([A-Z][A-Za-z0-9]+(?:[ -][A-Z][A-Za-z0-9]+){0,3})", body):
            after = body[m.end():]
            words = [w for w in re.split(r"[ -]", m.group(1)) if w not in CAPS_STOP and w.lower() not in skip]
            name = " ".join(words)
            if (not words or len(name) < 3 or name in NOT_NAMES or all(w in NOT_NAMES for w in words)
                    or RECRUITING.search(m.group(1))
                    or PLACE_WORDS.search(name) or ROLE_PLURAL.search(name)
                    or re.match(r"\s*[,(]\s*[A-Z]{2}\b|!", after)
                    or any(w.isupper() and len(w) > 4 for w in words)
                    or any(t["_re"].search(name if "regex" in t else name.lower()) for t in terms())):
                continue
            names[name] += 1
    good = [n for n, c in names.most_common() if c >= 2 or " " in n or re.search(r"[a-z][A-Z]", n)]
    return good[:limit]


# ---------------------------------------------------------------- brief

BROAD = ("This posting is broad: it names few specific tools, so we'll practice your projects, fundamentals and the "
         "why-us story.")
FACT_ORDER = ("pay", "where you work", "start date", "graduation", "experience", "work authorization",
              "interview process")


def ask_them(rec: dict) -> list[str]:
    """Questions the student can ask the interviewer, built from the posting (not the recruiter's logistics)."""
    who = "intern" if re.search(r"intern|co-?op|student|apprentice", rec["posting"].get("title") or "", re.I) \
        else "new hire"
    out = []
    keywords = rec.get("keywords") or []
    if keywords:
        out.append(f"What's coming up next for {keywords[0]}, and how would {'an' if who == 'intern' else 'a'} {who} "
                   "help?")
    hard = backed(rec["skills"])
    if hard:
        skill = hard[0]["term"]
        if hard[0]["cat"] == "concept" and skill[1:].islower():
            skill = skill.lower()  # "do with testing", but "do with Kafka"
        out.append(f"What does a strong {who} on this team do with {skill} in the first month?")
    out.append("What do you enjoy most about working on this team?")
    if len(out) < 3:
        out.append(f"What separates a good {who} from a great one here?")
    return out


def _details(rec):
    """Practical facts, one bullet per posting line: 'Pay, where you work, start date: "..." (line 20)'."""
    fx, lines = rec["facts"], rec["posting"]["lines"]
    by_line = {}
    for name in FACT_ORDER:
        if name in fx:
            by_line.setdefault(fx[name]["line"], []).append(name)
    out = []
    for n, names in by_line.items():
        quotes = {fx[x]["quote"] for x in names}
        text = quotes.pop() if len(quotes) == 1 else quote(lines[n - 1], None, 220)
        out.append(f"- {', '.join(names).capitalize()}: \"{text}\" (line {n})")
    return out


def brief_markdown(rec: dict, top_n: int = 5) -> str:
    p, skills, fx = rec["posting"], rec["skills"], rec["facts"]
    company, title = p["company"], p["title"]
    groups = grouped(backed(skills), top_n)  # a skill only the company pitch mentions goes under "Also mentioned"
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
            md.append(f"{n}. {_group_names(g, 4)} ({label}{times}): \"{g['quote']}\" (line {g['line']})")
    else:
        md.append(BROAD)
    if rest:
        md += ["", "## Also mentioned", ""]
        md += [f"- {s['term']}{' (nice to have)' if s['kind'] == 'nice' else ''}: line {s['line']}" for s in rest[:10]]
    md += ["", "## Practical details", ""] + _details(rec)
    missing = [ASK_RECRUITER[k] for k in ASK_RECRUITER if k not in fx]
    if missing:
        md += ["", "## Not in the posting: ask your recruiter", ""] + [f"- {m}" for m in missing]
    md += ["", "## Questions to practice", ""]
    for n, q in enumerate(plan_questions(rec)[:4], 1):
        md.append(f"{n}. {q['question']}" + (f" (because of line {q['line']})" if q.get("line") else ""))
    md += ["", "## Questions to ask them", ""] + [f"- {q}" for q in ask_them(rec)]
    keywords = rec.get("keywords") or []
    md += ["", "## A 30-second pitch outline", ""]
    md.append(f"- Who you are, plus one project that shows {lead[0]['term'] if lead else 'what the role needs'}")
    if len(lead) > 1:
        md.append(f"- Where you used {lead[1]['term']}" + (f" or {lead[2]['term']}" if len(lead) > 2 else ""))
    md.append(f"- Why {company}" + (f": mention \"{keywords[0]}\" from their posting" if keywords else ""))
    md += ["", "## The posting, numbered", ""]
    md += [f"{i}. {line}" for i, line in enumerate(p["lines"], 1)]
    return "\n".join(md) + "\n"


def brief_texts(rec: dict) -> list[str]:
    """The texts that go with the brief: what to expect, the top ask in the posting's own words, the rest."""
    p = rec["posting"]
    company, title = spoken_company(p["company"]), spoken_title(p["title"])
    if rec.get("when"):
        first = (f"{company} interview {rec['when']}. Your prep brief is ready." if company != NO_COMPANY
                 else f"Interview {rec['when']}. Your prep brief is ready.")
    else:
        named = ", ".join(x for x in (company, title) if x not in (NO_COMPANY, "this role"))
        first = f"{named}: your prep brief is ready." if named else "Your prep brief is ready."
    gate = gates(p["lines"])
    texts = [first]
    if gate:
        texts.append(f"Check this first: \"{quote(gate['quote'], None, 110)}\" (line {gate['line']})")
    if not backed(rec["skills"]):
        return texts + [BROAD]
    groups = grouped(backed(rec["skills"]), 4)
    top = groups[0]
    texts.append(f"Top of their list: {_group_names(top)}. In their words: \"{quote(top['quote'], None, 90)}\"")
    if len(groups) > 1:
        texts.append("Then " + "; ".join(_group_names(g) for g in groups[1:]) + ".")
    if len(backed(rec["skills"])) < 3:
        texts.append("Heads up: this posting names very few specific skills, so prep your projects and the why-us "
                     "story.")
    texts.append("The brief quotes the posting for every point, with line numbers so you can check it.")
    return texts


def headline(rec: dict) -> str:
    if not backed(rec["skills"]):
        return BROAD
    groups = grouped(backed(rec["skills"]), 3)
    first = groups[0]
    if len(first["terms"]) == 1 and first["mentions"] > 1:
        where = f" ({first['mentions']} mentions, line {first['line']})"
    else:
        where = f" (line {first['line']})"
    others = ", then ".join(_group_names(g) for g in groups[1:])
    return f"Their posting leans on {_group_names(first)}{where}" + (f", then {others}." if others else ".")


# ---------------------------------------------------------------- questions

def _fill(text, company, term=None):
    return text.replace("{company}", company or "this company").replace("{term}", term or "this")


def _named(rec):
    """The company as questions and labels say it: its plain name, or "this company"."""
    company = spoken_company(rec["posting"]["company"])
    return company if company != NO_COMPANY else "this company"


def _resolve_points(points, rec, term=None):
    company = (rec["posting"]["company"] or "").strip()
    named = _named(rec)
    top = (backed(rec["skills"]) or [s for s in rec["skills"] if s["kind"] != "nice"] or [None])[0]
    out = []
    for p in points:
        if (p.get("company") and not company) or (p.get("posting_keywords") and not rec.get("keywords")) or \
                (p.get("top_term") and not top):
            continue
        phrases = [x.lower() for x in p.get("any", [])]
        if p.get("term") and term:
            info = term_info(term)
            phrases += [term.lower()] + [a.lower() for a in (info or {}).get("aliases", [])]
        if p.get("company") or p.get("company_ok"):
            phrases.append(company.lower())
        if p.get("posting_keywords"):
            phrases += [k.lower() for k in rec.get("keywords") or []]  # answers are matched in lower case
        if p.get("top_term"):
            info = term_info(top["term"])
            phrases += [top["term"].lower()] + [a.lower() for a in (info or {}).get("aliases", [])]
        phrases = sorted({x.strip() for x in phrases if x.strip()})
        label = _fill(p["label"], named, term)
        if p.get("top_term"):
            label = f"mentions {top['term']} (the posting's top skill)"
        cues = [c.lower() for c in p.get("cues", [])] + [x for x in phrases if len(x) >= 6]
        point = {"label": label, "phrases": phrases, "number": bool(p.get("number")), "cite": _cite(rec, cues)}
        if p.get("follow_up"):
            point["follow_up"] = _fill(p["follow_up"], named, term)
        out.append(point)
    return out


def _points(entry, rec, term=None):
    """A question's points (the STAR ones first for a behavioral question); its own first point is the core."""
    own = _resolve_points(entry["points"], rec, term)
    pts = (_resolve_points(questions()["star_points"], rec, term) if entry.get("star") else []) + own
    if pts:
        (own or pts)[0]["core"] = True
    return pts


def _wrong(entry, named, term=None):
    """Misconceptions to listen for: saying one un-credits its point and adds a tip."""
    return [{"phrases": [x.lower() for x in w["any"]], "point": _fill(w["point"], named, term), "tip": w["tip"],
             "unless": _fill(w.get("unless") or "", named, term)} for w in entry.get("wrong", [])]


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


# A broad posting still gets a fundamentals question (with no posting line to cite).
FUNDAMENTALS = {"term": "Data structures and algorithms", "cat": "concept", "kind": "fundamentals", "line": None,
                "quote": None}


def plan_questions(rec: dict) -> list[dict]:
    """Every question available for this interview, in the order they should be asked."""
    named = _named(rec)
    bank = questions()
    tech, soft, projects = [], [], []

    def item(e, s):
        return {"id": e["id"], "question": _fill(e["q"], named, s["term"]), "term": s["term"], "line": s["line"],
                "quote": s["quote"], "points": _points(e, rec, s["term"]), "wrong": _wrong(e, named, s["term"]),
                "behavioral": bool(e.get("star")) or s["cat"] == "soft"}

    skills = [s for s in rec["skills"] if s["kind"] != "nice"]
    if not backed(rec["skills"]):
        skills = [FUNDAMENTALS] + [s for s in skills if s["term"] != FUNDAMENTALS["term"]]
    for s in skills:
        project = {"id": f"project-{slug(s['term'])}", **bank["generic_tech"]}
        entries = bank["terms"].get(s["term"])
        if s["cat"] == "soft":
            soft += [item(e, s) for e in entries or []]
        elif entries:
            tech += [item(e, s) for e in entries]
            if s["line"]:
                projects.append(item(project, s))  # a second, experience-based question on the same skill
        else:
            tech.append(item(project, s))
    none = {"term": None, "line": None, "quote": None, "cat": None}
    general = [item(e, none) for e in bank["company"]]
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
    if not any(q["behavioral"] for q in unique[:4]):  # one behavioral question early, not after every skill
        b = next((q for q in unique if q["behavioral"]), None)
        if b:
            unique.remove(b)
            unique.insert(3, b)
    return unique


TONES = {
    "chill": {
        "opener": "Hi, it's Cubbie with your {company} mock interview. No pressure, this is just practice.",
        "ask": "Here's your question. {q}",
        "close": "Take a second to think. When we hang up, text me your answer as a voice note. "
                 "About a minute is perfect.",
    },
    "tough": {
        "opener": "This is your {company} mock interview. Today I'll be a tough interviewer, so be specific.",
        "ask": "{q} Give me one real example.",
        "close": "When we hang up, send your answer as a voice note. Aim for 60 to 90 seconds and get to the point.",
    },
}


def _term_matches(asked, term):
    info = term_info(term or "") or {}
    return (asked or "").lower() in [term.lower()] + [a.lower() for a in info.get("aliases", [])]


def next_question(rec: dict, tone: str = "chill", question_id: str | None = None,
                  term: str | None = None) -> dict | None:
    plan = plan_questions(rec)
    asked = rec.get("asked", [])
    if question_id:
        pick = next((q for q in plan if q["id"] == question_id), None)
    elif term:
        pick = next((q for q in plan if q["term"] and _term_matches(term, q["term"]) and q["id"] not in asked), None)
    else:
        pick = next((q for q in plan if q["id"] not in asked), None)
    if pick is None:
        return None
    t = TONES.get(tone, TONES["chill"])
    company = spoken_company(rec["posting"]["company"])
    opener = t["opener"].replace("{company} ", "") if company == NO_COMPANY else _fill(t["opener"], company)
    why = f"The posting says \"{pick['quote']}\" (line {pick['line']})." if pick.get("line") else None
    pick = dict(pick, tone=tone, why=why)
    pick["call_script"] = " ".join([opener, t["ask"].replace("{q}", pick["question"]), t["close"]])
    pick["text_version"] = ("Mock question" + (f" for {company}" if company != NO_COMPANY else "") +
                            f":\n{pick['question']}\nAnswer with a voice note or a text whenever you're ready.")
    return pick


# ---------------------------------------------------------------- scoring

FILLERS = ["um", "uh", "you know", "kind of", "sort of", "basically", "literally", "i mean"]
COURSE_CODE = re.compile(r"\b[A-Z]{2,4} ?\d{3}[A-Z]?\b")
NAMED_NUMBER = re.compile(r"\b(?:cse|cs|cis|eee|ece|ser|ift|mat|math|stat|phy|csc|csci|eecs|comp|info|ee|me)"
                          r"\s?\d{3}[a-z]?\b|\b(?:python|oauth|html|css|http|ipv|windows|ios|android|java|php|vue|"
                          r"angular|swift|version|web|es|v)\s?\d+(?:\.\d+)*\b", re.I)
# Numbers that aren't results: years, ordinals ("3rd year"), "3D", and version numbers ("Ubuntu 22.04").
NOT_METRIC = re.compile(r"\b(?:19|20)\d\d\b|\b\d+(?:st|nd|rd|th|d)\b|\b\d+(?:\.\d+){1,3}\b(?!\s*(?:%|percent|x\b|"
                        r"times|ms\b|s\b|sec|minutes|hours|gb|mb))")
UNIT = (r"%|percent\b|x\b|times\b|ms\b|milliseconds?\b|s\b|secs?\b|seconds?\b|mins?\b|minutes?\b|hours?\b|hrs?\b|"
        r"days?\b|weeks?\b|months?\b|[kmgt]b\b|users?\b|requests?\b|rows?\b|records?\b|queries\b|calls\b|"
        r"transactions?\b|messages?\b|events?\b|customers?\b|people\b|students?\b|members?\b|downloads?\b|bugs?\b|"
        r"tickets?\b|lines\b|points\b|stars\b|servers?\b|orders?\b|errors?\b|clicks?\b|visits?\b|views?\b|dollars?\b")
# A result needs a metric context: a unit, "by/from/to N", a dollar amount, a large count, or a word like "twice".
METRIC = re.compile(r"(?<![a-z0-9.+#/-])\$?\d[\d,.]*\s*[km]?\s*(?:[a-z-]+\s+)?(?:" + UNIT + r")|"
                    r"\b(?:by|to|from|under|over|than|about|around|nearly|almost|only)\s+\$?\d|\$\s?\d|"
                    r"\b\d{1,3}(?:,\d{3})+\b|\b(?:percent|half|twice|double[ds]?|tripled?|hundreds?|thousands?|"
                    r"millions?|dozens?)\b")


def _phrase_re(p):
    """Two-letter phrases match whole words; 3-4 letters allow s/es/ed ("tests"); longer ones are stems
    ("idempoten")."""
    if len(p) <= 2:
        tail = r"(?![a-z0-9+#])"
    elif len(p) <= 4:
        tail = r"(?:s|es|ed)?(?![a-z0-9+#])"
    else:
        tail = ""
    return re.compile(rf"(?<![a-z0-9]){re.escape(p)}{tail}")


NEGATION = re.compile(r"\b(don't|do not|didn't|did not|never|not sure|no idea|no clue|not familiar with|"
                      r"unfamiliar with|"
                      r"no experience (?:with|in)|haven't|have not|not really|wouldn't|would not|won't|will not|"
                      r"don't think|no need|instead of|isn't|is not|aren't|can't|cannot|can not|lack|lacking|unsure|"
                      r"forgot|forget|nobody|no one)"
                      r"(?: [\w'-]+){0,6} $")
# A negating predicate right after the phrase: "a key would not help", "retries are impossible", "X? That's not
# something I know".
AFTER_NEG = re.compile(r"^[\w'-]*\s*(?:\?\s*(?:that's|that is|it's|it is) not something|"
                       r"(?:[\w'-]+ ){0,2}(?:(?:would|will|does|do|did|is|are|was)(?:n't| not)|won't|can't|cannot) "
                       r"(?:really )?(?:help|matter|work|apply|needed|necessary|be needed|something i)|"
                       r"(?:[\w'-]+ ){0,2}(?:is|are|would be|seems|sounds) (?:impossible|useless|pointless|"
                       r"unnecessary|not needed|not necessary))")
CLAUSE = re.compile(r".*(?:[.,;:!?]|\b(?:but|because|so|although|though|then|which)\b)", re.S)


def _said(phrase, text):
    """True when the answer uses the phrase, not counting "I don't know X", "instead of X" or "X wouldn't help"."""
    for m in _phrase_re(phrase).finditer(text):
        before = CLAUSE.sub("", text[max(0, m.start() - 70):m.start()])  # only the clause the phrase is in
        if not NEGATION.search(" " + before) and not AFTER_NEG.match(text[m.end():m.end() + 70]):
            return True
    return False


FUNCTION_WORDS = set("""i i'm i'd i'll i've me my we we'd we're our you your it it's its they them their he she the a
an to of in on at for with from by as and or but so if then than that this these those is are was were be been
would will can could should do does did have has had not no when because which what how why where there here just
also after before first""".split())


def _list_like(words, text):
    """A dump of key words: hardly any function words, or mostly short comma-separated fragments."""
    if len(words) < 8:
        return False
    share = sum(w in FUNCTION_WORDS for w in words) / len(words)
    pieces = [x for x in re.split(r"[,;\n]+", text) if x.strip()]
    return share < 0.2 or (share < 0.3 and len(words) / len(pieces) <= 3.5)


def verdict_for(covered: int, total: int, words: int | None = None, hedged: bool = False, list_like: bool = False,
                core_missed: bool = False) -> str:
    ratio = covered / (total or 1)
    verdict = "strong" if ratio >= 0.75 else "solid" if ratio > 0.5 else "needs work"
    if verdict == "strong" and (core_missed or hedged or (words is not None and words < 50)):
        verdict = "solid"  # a hedge, a missed core point, or barely a minute's worth isn't a strong answer
    if verdict != "needs work" and (list_like or (words is not None and words < 40)):
        verdict = "key words only"  # a list of the right words isn't an answer yet
    return verdict


HEDGE = re.compile(r"\b(not sure|no idea|i don't know|i guess|maybe)\b")
SOFT_HEDGE = re.compile(r"\b(maybe|probably|i think)\b")


def score_answer(question: dict, answer: str) -> dict:
    no_codes = " ".join(NAMED_NUMBER.sub(" ", COURSE_CODE.sub(" ", answer or "")).lower().split())  # "CSE 340"
    no_codes = NOT_METRIC.sub(" ", no_codes)
    text = " ".join((answer or "").lower().replace("’", "'").split())
    results = []
    for p in question["points"]:
        hit = next((ph for ph in p["phrases"] if _said(ph, text)), None)
        metric = METRIC.search(no_codes) if not hit and p.get("number") else None
        hit = hit or (metric.group(0).strip() if metric else None)
        results.append({"point": p["label"], "covered": bool(hit), "evidence": hit, "cite": p.get("cite")})
    tips = []
    for w in question.get("wrong") or []:  # a misconception un-credits its point (unless the fix was said too)
        said = next((ph for ph in w["phrases"] if _said(ph, text)), None)
        if said and not any(r["covered"] for r in results if r["point"] == w.get("unless")):
            tips.append(w["tip"])
            for r in results:
                if r["point"] == w["point"]:
                    r.update(covered=False, evidence=None, wrong=said)
    words = re.findall(r"[a-z0-9']+", text)
    fillers = {f: len(re.findall(rf"(?<![a-z]){re.escape(f)}(?![a-z])", text)) for f in FILLERS}
    fillers = {k: v for k, v in fillers.items() if v}
    i_count = sum(1 for w in words if w in ("i", "i'm", "i've", "i'd", "my"))
    we_count = sum(1 for w in words if w in ("we", "we're", "we've", "our"))
    covered = sum(r["covered"] for r in results)
    hedged, list_like = bool(HEDGE.search(text)), _list_like(words, text)
    core = next((p for p in question["points"] if p.get("core")), question["points"][0] if question["points"] else None)
    core_missed = bool(core) and not any(r["covered"] for r in results if r["point"] == core["label"])
    verdict = verdict_for(covered, len(results), len(words), hedged, list_like, core_missed)
    if list_like:
        tips.append("That reads like a list of key words. Say how they fit together: what you'd do first, and why.")
    elif len(words) < 40:
        tips.append("Too short. Aim for about a minute, around 120 to 180 words.")
    if re.search(r"\b(not sure|no idea|i guess)\b", text) or len(SOFT_HEDGE.findall(text)) >= 2:
        tips.append("Drop the hedging (\"not sure\", \"maybe\", \"I think\"); say what you'd do and why.")
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
        "points": results, "missed": missed, "words": len(words), "hedged": hedged, "list_like": list_like,
        "misconceptions": [t for t in tips if t in {w["tip"] for w in question.get("wrong") or []}],
        "fillers": fillers, "tips": tips, "cite": f"line {question['line']}" if question.get("line") else None,
        "term": question.get("term"),
    }


# ---------------------------------------------------------------- invite emails

REJECT = re.compile(r"(we (?:will|are|have) (?:not|decided not)[^.]{0,30}(?:moving forward|proceed|pursue)|"
                    r"decided not to (?:move|proceed)|"
                    r"(?:won'?t|will not|are not) (?:be )?(?:moving forward|proceeding)|"
                    r"not (?:be )?extending (?:you )?(?:an|the|this) offer|not (?:be )?moving forward with your|"
                    r"(?:decided|chosen) to (?:pursue|move forward with) other|decided to pursue (?:other )?"
                    r"(?:candidates|applicants)|moving forward with (?:other|another) (?:candidates?|applicants?)|"
                    r"(?:unable|not able) to offer you (?:a|an|the) (?:position|role|internship|job|offer)|"
                    r"(?:filled the (?:role|position)|position has been filled)(?!,?\s+but\b)|"
                    r"not able to move you forward|go with other (?:candidates|applicants)|"
                    r"won'?t be able to invite you|go in a different direction|we regret to inform|not been selected|"
                    r"were not selected)", re.I)
UNFORTUNATE = re.compile(r"\bunfortunately\b[^.!?\n]{0,160}?(?:\bnot\b|n't\b|\bunable\b|\bno longer\b|\bfilled\b|"
                         r"\bother (?:candidates?|applicants?)\b|\banother candidate\b|\bclosed\b)", re.I)
RESCHEDULE = re.compile(r"(reschedul|original time|move your interview|new time|different time)", re.I)
OFFER = re.compile(r"((?:pleased|excited|thrilled|delighted|happy|glad) to (?:extend|offer you)|"
                   r"extend(?:ing)? (?:you )?(?:an|this|our|a formal) offer|offer of (?:employment|internship))", re.I)
OFFER_DOC = re.compile(r"(offer letter|(?:formal|written) offer)", re.I)
INTERVIEW_OFFER = re.compile(r"offer(?:ing)? you an? (?:interview|phone|chance to interview|opportunity to interview)",
                             re.I)
APPLIED = re.compile(r"(thank(?:s| you) for (?:applying|your application|your interest)|received your application|"
                     r"application (?:has been|was) received|if (?:you are|you're) selected|will (?:contact|reach out "
                     r"to) you (?:to|if)|reviewing your application|under review)", re.I)
CONDITIONAL = re.compile(r"(if (?:you are|you're) selected|should you be selected|"
                         r"will (?:contact|reach out to|be in touch with) you if|"
                         r"if (?:we|your|there)[^.]{0,40}(?:move forward|match|fit|interview))", re.I)
INVITE = re.compile(r"(invite you|invitation to|"
                    r"(?:like|love) to (?:invite|schedule|move you|set up|have you|interview)|"
                    r"next round|final round|move you (?:forward|to the next)|moving you forward|"
                    r"share your availability|send (?:us |over )?your availability|(?:have been|were|been) selected "
                    r"(?:for|to)|offer you an? interview)", re.I)
ASSESS = re.compile(r"(online assessment|coding assessment|hackerrank|codesignal|codility|hirevue|take[- ]home|"
                    r"technical assessment|\bOA\b)", re.I)
ASSESS_DONE = re.compile(r"(thank(?:s| you) for (?:completing|taking|submitting)|completed (?:the|your)|"
                         r"you(?:'ve| have) completed|after reviewing your)", re.I)
SCHEDULE = re.compile(r"(calendly\.com|goodtime\.io|schedul(e|ing) (link|your|an interview|a time)|"
                      r"select (a|your) time|"
                      r"pick a time|share your availability|your availability|book (?:a|your) (?:time|slot|interview)|"
                      r"choose a time)", re.I)
SCHEDULE_LINK = re.compile(r"calendly\.com/|goodtime\.io/", re.I)
CONFIRMED = re.compile(r"(confirmed|is scheduled|has been scheduled|calendar invit|see you (on|at)|zoom\.us/|"
                       r"meet\.google\.com|teams\.microsoft\.com|join the (call|meeting))", re.I)
INTERVIEW = re.compile(r"(interview|phone screen|technical screen|onsite|on-site|super ?day|final round|next round|"
                       r"recruiter (call|chat))", re.I)
URL = re.compile(r"https?://[^\s<>\"')\]]+")
# Mail hosts, scheduling tools and applicant-tracking vendors: never the company the interview is with
NOT_COMPANY = {"gmail", "outlook", "hotmail", "yahoo", "icloud", "greenhouse", "greenhouse-mail", "lever", "hire",
               "ashbyhq", "ashby", "goodtime", "myworkday", "myworkdayjobs", "workday", "smartrecruiters", "calendly",
               "handshake", "joinhandshake", "linkedin", "indeed", "asu", "edu", "mail", "notifications", "us",
               "hirevue", "icims", "taleo", "zoom", "jobvite", "successfactors", "workable", "gem", "paradox",
               "eightfold", "avature", "phenompeople", "bamboohr", "recruitee", "talent", "careers", "recruiting",
               "noreply", "no-reply", "email", "e", "t", "info", "jobs", "app", "mg", "em", "bounce"}
NEXT_STEP = {
    "rejection": "Not an invite. Don't start prep; be kind if the student brings it up.",
    "offer": "A job offer, not an interview. Congratulate the student; there's nothing to prep.",
    "application_received": "They got the application; no interview yet. Nothing to prep; offer to watch for the "
                            "invite.",
    "assessment": "An online assessment, not an interview. Offer practice for the skills in the posting instead of a "
                  "mock call.",
    "interview_confirmed": "An interview with a time. Confirm it with the student, then fetch the posting and offer "
                           "the brief.",
    "schedule_request": "They need to pick a time first. Confirm with the student before offering time slots, then "
                        "fetch the posting.",
    "interview": "Probably an invite. Confirm the company, role and time with the student, then fetch the posting.",
    "other": "Not an interview email.",
}


def _category(text):
    unfortunate_only = UNFORTUNATE.search(text) and not (INTERVIEW.search(text) or OFFER.search(text) or
                                                         OFFER_DOC.search(text) or SCHEDULE.search(text) or
                                                         CONFIRMED.search(text))
    if (REJECT.search(text) or unfortunate_only) and not RESCHEDULE.search(text):
        return "rejection", "rejection wording"
    if not INTERVIEW_OFFER.search(text) and (OFFER.search(text) or (OFFER_DOC.search(text) and
                                                                   not INTERVIEW.search(text))):
        return "offer", "offer wording"
    if APPLIED.search(text) and not CONFIRMED.search(text) and not SCHEDULE_LINK.search(text) and \
            (CONDITIONAL.search(text) or not INVITE.search(text)):
        return "application_received", "confirms the application, no invite yet"
    if ASSESS.search(text) and not CONFIRMED.search(text) and not (ASSESS_DONE.search(text) and INVITE.search(text)):
        return "assessment", "online assessment"
    if INTERVIEW.search(text) and CONFIRMED.search(text):
        return "interview_confirmed", "interview with a confirmed time or meeting link"
    if INTERVIEW.search(text) and SCHEDULE.search(text):
        return "schedule_request", "asks the student to pick a time"
    if INTERVIEW.search(text):
        return "interview", "mentions an interview"
    return "other", None


def company_hint(text: str) -> str | None:
    """The company from the sender's address (saguaropay.icims.com -> Saguaropay), skipping mail and hiring tools."""
    sender = re.search(r"^from:[^\n@]{0,200}@([a-z0-9.-]+)", text, re.I | re.M)
    if not sender:
        return None
    parts = [x for x in sender.group(1).lower().split(".")
             if x not in ("com", "io", "co", "org", "net", "ai", "uk", "us", "ca", "in", "de", "edu", "gov")]
    for part in reversed(parts):
        if part and part not in NOT_COMPANY and not re.fullmatch(r"wd\d+|\d+", part):
            return part.capitalize()
    return None


def classify_email(text: str, parse_link: Callable[[str], dict]) -> dict:
    """Is this an interview invite, a request to schedule, an assessment, an offer, an application receipt, a
    rejection, or something else? The category is a hint: Cubbie confirms with the student before acting on it."""
    category, signal = _category(text)
    job_links = []
    for url in dict.fromkeys(URL.findall(text)):
        url = re.sub(r"^(https?://)[^/@\s]*@", r"\1", url.rstrip(".,;"))  # never echo a user:password@ part
        try:
            link = parse_link(url)
        except Exception:
            continue
        if link["board"] != "webpage" or re.search(r"/(jobs?|careers?|positions?|openings?)/", url, re.I):
            job_links.append({"url": url, "board": link["board"]})
    return {"category": category, "signals": [signal] if signal else [], "job_links": job_links,
            "company_hint": company_hint(text), "next": NEXT_STEP[category]}
