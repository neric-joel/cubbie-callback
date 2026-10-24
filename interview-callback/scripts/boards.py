"""Fetch job postings from public job-board APIs (no keys) and turn them into numbered plain-text lines.

Supported: Greenhouse, Lever, Ashby, SmartRecruiters and Workday links, a search by company + role on the
first four (plus a few known Workday tenants), and any other career page that publishes schema.org JobPosting data.
"""

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser

USER_AGENT = "interview-callback/1.0 (+https://github.com/neric-joel/cubbie-callback)"
TIMEOUT = 20

# Workday tenants checked by hand: company key -> (tenant, wd host number, career site)
WORKDAY_TENANTS = {
    "intel": ("intel", "wd1", "External"),
}


class BoardError(Exception):
    """kind: not_found | closed | ambiguous | unreachable | bad_input"""

    def __init__(self, kind, message, candidates=None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.candidates = candidates or []


def http_get(url, data=None, headers=None):
    """Return (status, body). 404/410 come back as a status; network failures raise BoardError."""
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return e.code, ""
        raise BoardError("unreachable", f"{urllib.parse.urlsplit(url).netloc} answered HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise BoardError("unreachable", f"could not reach {urllib.parse.urlsplit(url).netloc}: {e}")


def get_json(url, data=None, headers=None):
    status, body = http_get(url, data=data, headers=headers)
    if status != 200:
        return status, None
    try:
        return status, json.loads(body)
    except ValueError:
        raise BoardError("unreachable", f"{urllib.parse.urlsplit(url).netloc} did not return JSON")


# ---------------------------------------------------------------- HTML -> numbered lines

class _TextParser(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table",
             "section", "article", "header", "footer", "blockquote", "pre", "hr"}
    SKIP = {"script", "style", "noscript", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n- " if tag == "li" else "\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


PUNCT = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": " - ",
                       "\xa0": " ", "​": ""})


def _split_long(line, limit=280):
    if len(line) <= limit:
        return [line]
    out, cur = [], ""
    for sentence in re.split(r"(?<=[.!?;])\s+", line):
        if cur and len(cur) + len(sentence) + 1 > limit:
            out.append(cur)
            cur = sentence
        else:
            cur = f"{cur} {sentence}".strip()
    if cur:
        out.append(cur)
    return out


def html_to_lines(fragment):
    """Plain-text lines of an HTML fragment: one per paragraph, list item or heading; long ones split by sentence."""
    parser = _TextParser()
    parser.feed(html.unescape(fragment or "") if "&lt;" in (fragment or "") else (fragment or ""))
    parser.close()
    lines = []
    for raw in "".join(parser.parts).split("\n"):
        line = re.sub(r"\s+", " ", raw.translate(PUNCT)).strip()
        if line in ("", "-"):
            continue
        lines.extend(_split_long(line))
    return lines


def text_to_lines(text):
    lines = []
    for raw in (text or "").splitlines():
        line = re.sub(r"\s+", " ", raw.translate(PUNCT)).strip()
        if line:
            lines.extend(_split_long(line))
    return lines


# ---------------------------------------------------------------- links

def parse_link(url):
    """Which board a job link belongs to and the ids needed to fetch it."""
    url = (url or "").strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise BoardError("bad_input", f"not a web link: {url!r}")
    host = parts.netloc.lower().split(":")[0]
    segs = [s for s in parts.path.split("/") if s]
    query = urllib.parse.parse_qs(parts.query)

    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io"):
        if segs[:1] == ["embed"] and "for" in query and "token" in query:
            return {"board": "greenhouse", "slug": query["for"][0], "job_id": query["token"][0]}
        if len(segs) >= 3 and segs[1] == "jobs":
            return {"board": "greenhouse", "slug": segs[0], "job_id": segs[2]}
    if "gh_jid" in query:
        # A company career site that embeds Greenhouse, like stripe.com/jobs/...?gh_jid=123
        domain = host[4:] if host.startswith("www.") else host
        return {"board": "greenhouse", "slug": domain.split(".")[0], "job_id": query["gh_jid"][0]}
    if host == "jobs.lever.co" and len(segs) >= 2:
        return {"board": "lever", "slug": segs[0], "job_id": segs[1]}
    if host == "jobs.ashbyhq.com" and len(segs) >= 2:
        return {"board": "ashby", "slug": segs[0], "job_id": segs[1]}
    if host == "jobs.smartrecruiters.com" and len(segs) >= 2:
        job_id = re.match(r"\d+", segs[1])
        if job_id:
            return {"board": "smartrecruiters", "slug": segs[0], "job_id": job_id.group(0)}
    wd = re.match(r"^([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com$", host)
    if wd and "job" in segs:
        i = segs.index("job")
        site_segs = [s for s in segs[:i] if not re.match(r"^[a-z]{2}-[A-Z]{2}$", s)]
        if site_segs:
            return {"board": "workday", "slug": wd.group(1), "wd": wd.group(2), "site": site_segs[-1],
                    "job_path": "/".join(segs[i:])}
    return {"board": "webpage", "url": url}


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _posting(board, company, title, location, source_url, body_lines, extra=None):
    if not body_lines:
        raise BoardError("not_found", "the posting has no description text")
    posting = {"board": board, "company": company or "", "title": title or "", "location": location or "",
               "source_url": source_url, "fetched_at": _now(), "lines": body_lines}
    posting.update(extra or {})
    return posting


# ---------------------------------------------------------------- one posting per board

def posting_from_greenhouse(d, slug, company=None):
    return _posting("greenhouse", d.get("company_name") or company or pretty_company(slug), d.get("title"),
                    (d.get("location") or {}).get("name"), d.get("absolute_url"), html_to_lines(d.get("content")))


def fetch_greenhouse(slug, job_id, company=None):
    status, d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}")
    if status in (404, 410) or d is None:
        raise BoardError("closed", f"Greenhouse job {job_id} on board '{slug}' is closed or does not exist")
    return posting_from_greenhouse(d, slug, company)


def fetch_lever(slug, job_id, company=None):
    status, d = get_json(f"https://api.lever.co/v0/postings/{slug}/{job_id}")
    if status in (404, 410) or d is None:
        raise BoardError("closed", f"Lever posting {job_id} on '{slug}' is closed or does not exist")
    body = d.get("description") or ""
    for block in d.get("lists") or []:
        body += f"<h3>{block.get('text', '')}</h3><ul>{block.get('content', '')}</ul>"
    body += d.get("additional") or ""
    return _posting("lever", company or pretty_company(slug), d.get("text"), (d.get("categories") or {}).get("location"),
                    d.get("hostedUrl"), html_to_lines(body))


def fetch_ashby(slug, job_id, company=None):
    status, d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if status in (404, 410) or d is None:
        raise BoardError("not_found", f"no Ashby job board named '{slug}'")
    for job in d.get("jobs") or []:
        if job.get("id") == job_id or (job.get("jobUrl") or "").rstrip("/").endswith(job_id):
            return _posting("ashby", company or pretty_company(slug), job.get("title"), job.get("location"), job.get("jobUrl"),
                            html_to_lines(job.get("descriptionHtml")) or text_to_lines(job.get("descriptionPlain")))
    raise BoardError("closed", f"Ashby job {job_id} is no longer on '{slug}' (closed or unlisted)")


def fetch_smartrecruiters(slug, job_id, company=None):
    status, d = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{job_id}")
    if status in (404, 410) or d is None:
        raise BoardError("closed", f"SmartRecruiters posting {job_id} for '{slug}' is closed or does not exist")
    sections = ((d.get("jobAd") or {}).get("sections") or {})
    body = "".join(f"<h3>{s.get('title', '')}</h3>{s.get('text', '')}"
                   for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
                   for s in [sections.get(key) or {}] if s.get("text"))
    loc = d.get("location") or {}
    return _posting("smartrecruiters", (d.get("company") or {}).get("name") or company or pretty_company(slug), d.get("name"),
                    ", ".join(x for x in (loc.get("city"), loc.get("region")) if x),
                    d.get("postingUrl") or f"https://jobs.smartrecruiters.com/{slug}/{job_id}", html_to_lines(body))


def fetch_workday(slug, wd, site, job_path, company=None):
    base = f"https://{slug}.{wd}.myworkdayjobs.com"
    status, d = get_json(f"{base}/wday/cxs/{slug}/{site}/{job_path}")
    if status in (404, 410) or d is None:
        raise BoardError("closed", f"Workday job {job_path} on {slug} is closed or does not exist")
    info = d.get("jobPostingInfo") or {}
    return _posting("workday", company or pretty_company(slug), info.get("title"), info.get("location"),
                    info.get("externalUrl") or f"{base}/{site}/{job_path}", html_to_lines(info.get("jobDescription")))


class _JsonLd(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.blocks = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self.inside = True
            self.blocks.append("")
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside = False
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self.inside:
            self.blocks[-1] += data
        elif self._in_title:
            self.title += data


def _find_jobposting(node):
    if isinstance(node, dict):
        kind = node.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            return node
        for value in node.values():
            found = _find_jobposting(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_jobposting(value)
            if found:
                return found
    return None


def fetch_webpage(url, company=None):
    status, body = http_get(url)
    if status in (404, 410):
        raise BoardError("closed", "that job page is gone (closed or moved)")
    parser = _JsonLd()
    parser.feed(body)
    for block in parser.blocks:
        try:
            job = _find_jobposting(json.loads(block))
        except ValueError:
            continue
        if job:
            org = job.get("hiringOrganization") or {}
            loc = job.get("jobLocation") or {}
            if isinstance(loc, list):
                loc = loc[0] if loc else {}
            addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
            return _posting("webpage", company or (org.get("name") if isinstance(org, dict) else ""),
                            job.get("title"), ", ".join(x for x in (addr.get("addressLocality"), addr.get("addressRegion")) if x),
                            url, html_to_lines(job.get("description")))
    raise BoardError("unreachable", "that page has no machine-readable job description; read it with the web "
                                    "ability and pass the text with --text-file")


def fetch_by_link(url, company=None):
    link = parse_link(url)
    board = link["board"]
    if board == "greenhouse":
        return fetch_greenhouse(link["slug"], link["job_id"], company)
    if board == "lever":
        return fetch_lever(link["slug"], link["job_id"], company)
    if board == "ashby":
        return fetch_ashby(link["slug"], link["job_id"], company)
    if board == "smartrecruiters":
        return fetch_smartrecruiters(link["slug"], link["job_id"], company)
    if board == "workday":
        return fetch_workday(link["slug"], link["wd"], link["site"], link["job_path"], company)
    return fetch_webpage(url, company)


# ---------------------------------------------------------------- search by company + role

ROLE_EXPANSIONS = {
    "swe": "software engineer", "sde": "software develop engineer", "pm": "product manager",
    "ml": "machine learning", "ai": "artificial intelligence", "ds": "data scientist", "internship": "intern",
    "interns": "intern", "co": "", "op": "intern", "coop": "intern", "eng": "engineer", "engineering": "engineer",
    "dev": "develop", "developer": "develop", "development": "develop", "jr": "junior", "sr": "senior",
}
ROLE_NOISE = {"summer", "fall", "spring", "winter", "the", "a", "an", "and", "or", "of", "for", "at", "role", "position",
              "us", "usa", "new", "grad"}


def normalize_title(text):
    words = re.findall(r"[a-z0-9+#]+", (text or "").lower())
    out = []
    for w in words:
        if re.fullmatch(r"20\d\d", w) or w in ROLE_NOISE:
            continue
        out.extend(ROLE_EXPANSIONS.get(w, w).split())
    return out


def same_role(a, b):
    return normalize_title(a) == normalize_title(b)


def pretty_company(slug):
    return " ".join(w.capitalize() for w in re.split(r"[-_]", slug or "") if w)


def title_score(role, title):
    want, have = normalize_title(role), normalize_title(title)
    if not want or not have:
        return 0.0
    coverage = sum(1 for w in set(want) if w in have) / len(set(want))
    precision = sum(1 for w in set(have) if w in want) / len(set(have))
    ratio = SequenceMatcher(None, " ".join(want), " ".join(have)).ratio()
    return round(0.5 * coverage + 0.2 * precision + 0.3 * ratio, 3)


def slug_candidates(company):
    base = re.sub(r"[^a-z0-9 &-]", "", (company or "").lower()).replace("&", "and").strip()
    base = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|technologies|labs)\b", "", base).strip()
    words = base.split()
    cands = ["".join(words), "-".join(words), words[0] if words else ""]
    return [c for i, c in enumerate(cands) if c and c not in cands[:i]]


def _list_greenhouse(slug):
    status, d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    if not d:
        return []
    return [{"board": "greenhouse", "title": j.get("title"), "url": j.get("absolute_url"),
             "location": (j.get("location") or {}).get("name"),
             "fetch": {"board": "greenhouse", "slug": slug, "job_id": str(j.get("id"))}} for j in d.get("jobs") or []]


def _list_lever(slug):
    status, d = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not isinstance(d, list):
        return []
    return [{"board": "lever", "title": j.get("text"), "url": j.get("hostedUrl"),
             "location": (j.get("categories") or {}).get("location"),
             "fetch": {"board": "lever", "slug": slug, "job_id": j.get("id")}} for j in d]


def _list_ashby(slug):
    status, d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if not d:
        return []
    return [{"board": "ashby", "title": j.get("title"), "url": j.get("jobUrl"), "location": j.get("location"),
             "fetch": {"board": "ashby", "slug": slug, "job_id": j.get("id")}} for j in d.get("jobs") or []]


def _list_smartrecruiters(slug, role):
    q = urllib.parse.quote(role)
    status, d = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?q={q}&limit=100")
    if not d:
        return []
    return [{"board": "smartrecruiters", "title": j.get("name"),
             "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}", "location": (j.get("location") or {}).get("city"),
             "fetch": {"board": "smartrecruiters", "slug": slug, "job_id": str(j.get("id"))}} for j in d.get("content") or []]


def _list_workday(key, role):
    tenant, wd, site = WORKDAY_TENANTS[key]
    payload = json.dumps({"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": role}).encode()
    status, d = get_json(f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs", data=payload,
                         headers={"Content-Type": "application/json"})
    if not d:
        return []
    out = []
    for j in d.get("jobPostings") or []:
        path = (j.get("externalPath") or "").lstrip("/")
        out.append({"board": "workday", "title": j.get("title"), "location": j.get("locationsText"),
                    "url": f"https://{tenant}.{wd}.myworkdayjobs.com/{site}/{path}",
                    "fetch": {"board": "workday", "slug": tenant, "wd": wd, "site": site, "job_path": path}})
    return out


def search(company, role, location=None, limit=5):
    """Candidate postings for a company + role, best title match first."""
    jobs = []
    for slug in slug_candidates(company):
        if slug in WORKDAY_TENANTS:
            jobs += _list_workday(slug, role)
        for lister in (_list_greenhouse, _list_lever, _list_ashby):
            jobs += lister(slug)
        if not jobs:
            jobs += _list_smartrecruiters(slug, role)
        if jobs:
            break
    if not jobs:
        raise BoardError("not_found", f"no public Greenhouse, Lever, Ashby or SmartRecruiters board found for "
                                      f"'{company}'")
    for j in jobs:
        j["score"] = title_score(role, j["title"])
        if location and j.get("location") and location.lower() in str(j["location"]).lower():
            j["score"] = round(j["score"] + 0.05, 3)
    jobs.sort(key=lambda j: -j["score"])
    best = jobs[0]["title"]
    same_title = [j for j in jobs if same_role(j["title"], best)][:12]
    return same_title + [j for j in jobs if not same_role(j["title"], best)][:limit]


US_STATE = re.compile(r"\b(US|USA|AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|"
                      r"NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b")
US_PLACE = re.compile(r"\b(united states|remote in the us|us remote|remote|seattle|san francisco|new york|nyc|chicago|"
                      r"austin|phoenix|tempe|chandler|scottsdale|boston|los angeles|denver|atlanta|dallas|"
                      r"washington|san jose|mountain view|palo alto|menlo park|redmond|portland|miami)\b", re.I)


def is_us(location):
    text = str(location or "")
    return bool(US_STATE.search(text) or US_PLACE.search(text))


def choose(candidates, min_score=0.75, margin=0.06, location=None):
    """The clear winner among search candidates, or None when the student has to pick.

    Several postings with the same title (one per office) count as one match; the one in the student's location,
    else a US one, is used, and the caller tells the student which office it picked.
    """
    if not candidates or candidates[0]["score"] < min_score:
        return None
    top = candidates[0]
    same = [c for c in candidates if same_role(c["title"], top["title"])]
    rivals = [c for c in candidates if not same_role(c["title"], top["title"]) and top["score"] - c["score"] < margin]
    if rivals:
        return None
    if location:
        local = [c for c in same if location.lower() in str(c.get("location") or "").lower()]
        if local:
            return local[0]
    us = [c for c in same if is_us(c.get("location"))]
    return (us or same)[0]


def fetch_candidate(candidate, company=None):
    f = candidate["fetch"]
    if f["board"] == "greenhouse":
        return fetch_greenhouse(f["slug"], f["job_id"], company)
    if f["board"] == "lever":
        return fetch_lever(f["slug"], f["job_id"], company)
    if f["board"] == "ashby":
        return fetch_ashby(f["slug"], f["job_id"], company)
    if f["board"] == "smartrecruiters":
        return fetch_smartrecruiters(f["slug"], f["job_id"], company)
    return fetch_workday(f["slug"], f["wd"], f["site"], f["job_path"], company)
