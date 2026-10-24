"""Fetch job postings from public job-board APIs (no keys) and turn them into numbered plain-text lines.

Supported: Greenhouse, Lever, Ashby, SmartRecruiters and Workday links, a search by company + role on the
first four (plus a few known Workday tenants), and any other career page that publishes schema.org JobPosting data.
"""

import html
import http.client
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser

USER_AGENT = "interview-callback/1.0 (+https://github.com/neric-joel/cubbie-callback)"
TIMEOUT = 20
MAX_BYTES = 20_000_000  # big boards (Palantir on Lever) list every description
MAX_LINES = 1500
DEADLINE = None  # time.monotonic() value after which no new request starts; set by the CLI

# Workday career sites checked by hand (Oct 24 2026): tenant -> (wd host, career site, display name)
WORKDAY_SITES = {
    "intel": ("wd1", "External", "Intel"),
    "boeing": ("wd1", "EXTERNAL_CAREERS", "Boeing"),
    "globalhr": ("wd5", "REC_RTX_Ext_Gateway", "RTX"),
    "ngc": ("wd1", "Northrop_Grumman_External_Site", "Northrop Grumman"),
    "wf": ("wd1", "WellsFargoJobs", "Wells Fargo"),
    "paypal": ("wd1", "jobs", "PayPal"),
    "salesforce": ("wd12", "External_Career_Site", "Salesforce"),
    "nvidia": ("wd5", "NVIDIAExternalCareerSite", "NVIDIA"),
    "capitalone": ("wd12", "Capital_One", "Capital One"),
    "micron": ("wd1", "External", "Micron"),
    "dexcom": ("wd1", "Dexcom", "Dexcom"),
}
# What a student might call the company (after slug_candidates) -> Workday tenant
WORKDAY_ALIASES = {
    "intel": "intel", "boeing": "boeing", "rtx": "globalhr", "raytheon": "globalhr", "collinsaerospace": "globalhr",
    "prattwhitney": "globalhr", "northropgrumman": "ngc", "northrop": "ngc", "ngc": "ngc", "wellsfargo": "wf",
    "paypal": "paypal", "salesforce": "salesforce", "nvidia": "nvidia", "capitalone": "capitalone",
    "micron": "micron", "microntechnology": "micron", "dexcom": "dexcom",
}
WORKDAY_TENANTS = {t: (t, wd, site) for t, (wd, site, _) in WORKDAY_SITES.items()}


class BoardError(Exception):
    """kind: not_found | closed | ambiguous | unreachable | bad_input"""

    def __init__(self, kind, message, candidates=None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.candidates = candidates or []


_PUBLIC = {}


def check_public(url):
    """Refuse links that point into a private network (the sandbox, a router, a cloud metadata service)."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise BoardError("bad_input", "only web links (https://...) can be fetched")
    if host not in _PUBLIC:
        try:
            addrs = {info[4][0] for info in socket.getaddrinfo(host, None)}
        except (socket.gaierror, UnicodeError, ValueError):
            raise BoardError("unreachable", f"could not find the website {host}")
        _PUBLIC[host] = all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs)
    if not _PUBLIC[host]:
        raise BoardError("bad_input", f"{host} is a private network address, not a public job page")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirect)


def _open(req, timeout):
    return _OPENER.open(req, timeout=timeout)


def http_get(url, data=None, headers=None):
    """Return (status, body). 404/410 come back as a status; every other failure raises BoardError."""
    check_public(url)
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9"}
    hdrs.update(headers or {})
    host = urllib.parse.urlsplit(url).netloc
    for attempt in (1, 2):
        left = TIMEOUT if DEADLINE is None else min(TIMEOUT, DEADLINE - time.monotonic())
        if left < 1:
            raise BoardError("unreachable", "ran out of time talking to the job boards")
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with _open(req, left) as resp:
                return resp.status, resp.read(MAX_BYTES).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return e.code, ""
            if e.code == 429 and attempt == 1:  # rate limited: wait as asked (at most 5 s) and try once more
                wait = (e.headers.get("Retry-After") if e.headers else None) or "2"
                time.sleep(min(5.0, float(wait)) if wait.replace(".", "", 1).isdigit() else 2.0)
                continue
            raise BoardError("unreachable", f"{host} answered HTTP {e.code}")
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError) as e:
            raise BoardError("unreachable", f"could not read {host}: {type(e).__name__}")


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
        self.li = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "li":
            self.li += 1
            self.parts.append("\n- ")
        elif tag in self.BLOCK:
            # <li><p>text</p></li> stays one bullet line
            self.parts.append(" " if self.li and tag in ("p", "div", "span", "br") else "\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag == "li":
            self.li = max(0, self.li - 1)
            self.parts.append("\n")
        elif tag in self.BLOCK:
            self.parts.append(" " if self.li and tag in ("p", "div", "span", "br") else "\n")

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
        line = re.sub(r"^[\u2022\u25aa\u25cf\u25e6\u00b7*\u2023\u2043]\s*", "- ", line)
        if line:
            lines.extend(_split_long(line))
    return lines


# ---------------------------------------------------------------- links

def parse_link(url):
    """Which board a job link belongs to and the ids needed to fetch it."""
    url = (url or "").strip().rstrip(".,;:!?)]}>\"'")
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        raise BoardError("bad_input", f"not a web link: {url!r}")
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
        if len(segs) == 1 and segs[0] != "embed":
            return {"board": "greenhouse", "slug": segs[0], "job_id": None}
    if "gh_jid" in query:
        # A company career site that embeds Greenhouse, like careers.airbnb.com/...?gh_jid=123: the board name is a
        # guess from the domain, so the page itself is the fallback.
        domain = re.sub(r"^(www|careers|jobs|boards|job-boards|apply)\.", "", host)
        job_id = re.sub(r"\D", "", query["gh_jid"][0])
        if job_id:
            return {"board": "greenhouse", "slug": domain.split(".")[0], "job_id": job_id, "guessed": url}
    if host == "jobs.lever.co" and segs:
        return {"board": "lever", "slug": segs[0], "job_id": segs[1] if len(segs) >= 2 else None}
    if host == "jobs.ashbyhq.com" and segs:
        return {"board": "ashby", "slug": segs[0], "job_id": segs[1] if len(segs) >= 2 else None}
    site = _eightfold_site(host)
    if site and ("job" in segs or "pid" in query):
        job_id = query["pid"][0] if "pid" in query else segs[segs.index("job") + 1] if segs.index("job") + 1 < len(segs) else ""
        if job_id.isdigit():
            return {"board": "eightfold", "host": host, "domain": site[1], "job_id": job_id}
    if host in ("amazon.jobs", "www.amazon.jobs") and "jobs" in segs[:-1]:
        job_id = segs[segs.index("jobs") + 1]
        if job_id.isdigit():
            return {"board": "amazon", "slug": "amazon", "job_id": job_id}
    if host == "jobs.smartrecruiters.com" and len(segs) >= 2:
        job_id = re.match(r"\d+", segs[1])
        if job_id:
            return {"board": "smartrecruiters", "slug": segs[0], "job_id": job_id.group(0)}
    wd = re.match(r"^([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com$", host)
    if wd and "job" in segs:
        while segs and segs[-1].lower() in ("apply", "applymanually", "autofillwithresume", "useMyLastApplication".lower()):
            segs = segs[:-1]
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
               "source_url": source_url, "fetched_at": _now(), "lines": body_lines[:MAX_LINES]}
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


def _workday_location(info):
    extra = [x for x in info.get("additionalLocations") or [] if x]
    return "; ".join([info.get("location") or ""] + extra).strip("; ")


def fetch_workday(slug, wd, site, job_path, company=None):
    base = f"https://{slug}.{wd}.myworkdayjobs.com"
    status, d = get_json(f"{base}/wday/cxs/{slug}/{site}/{job_path}")
    if status in (404, 410) or d is None:
        raise BoardError("closed", f"Workday job {job_path} on {slug} is closed or does not exist")
    info = d.get("jobPostingInfo") or {}
    name = WORKDAY_SITES.get(slug, (None, None, pretty_company(slug)))[2]
    return _posting("workday", company or name, info.get("title"), _workday_location(info),
                    info.get("externalUrl") or f"{base}/{site}/{job_path}", html_to_lines(info.get("jobDescription")))


# Eightfold career sites: key -> (host, domain parameter, display name)
EIGHTFOLD_SITES = {
    "microsoft": ("apply.careers.microsoft.com", "microsoft.com", "Microsoft"),
    "lockheedmartin": ("lockheedmartin.eightfold.ai", "lockheedmartin.com", "Lockheed Martin"),
}
EIGHTFOLD_ALIASES = {"microsoft": "microsoft", "msft": "microsoft", "lockheedmartin": "lockheedmartin",
                     "lockheed": "lockheedmartin", "lmco": "lockheedmartin"}


def _eightfold_site(host):
    for key, (h, domain, name) in EIGHTFOLD_SITES.items():
        if host == h:
            return key, domain, name
    if host.endswith(".eightfold.ai"):
        sub = host.split(".")[0]
        return sub, f"{sub}.com", pretty_company(sub)
    return None


def fetch_eightfold(host, domain, job_id, company=None):
    status, d = get_json(f"https://{host}/api/pcsx/position_details?position_id={job_id}&domain={domain}&hl=en")
    data = (d or {}).get("data") or {}
    if status in (404, 410) or not data.get("jobDescription"):
        raise BoardError("closed", f"posting {job_id} on {host} is closed or does not exist")
    name = (_eightfold_site(host) or (None, None, pretty_company(host.split(".")[0])))[2]
    return _posting("eightfold", company or name, data.get("name"), "; ".join(data.get("locations") or []),
                    data.get("publicUrl") or f"https://{host}/careers/job/{job_id}", html_to_lines(data["jobDescription"]))


def _list_eightfold(key, role):
    host, domain, _ = EIGHTFOLD_SITES[key]
    q = urllib.parse.quote(role)
    status, d = get_json(f"https://{host}/api/pcsx/search?domain={domain}&query={q}&location=&start=0&num=20")
    return [{"board": "eightfold", "title": p.get("name"), "location": "; ".join(p.get("locations") or []),
             "url": f"https://{host}/careers/job/{p.get('id')}",
             "fetch": {"board": "eightfold", "host": host, "domain": domain, "job_id": str(p.get("id"))}}
            for p in (((d or {}).get("data") or {}).get("positions") or [])]


# Career sites with a public jobs API at /api/jobs (State Farm's): key -> (host, display name)
JOBS_API_SITES = {"statefarm": ("jobs.statefarm.com", "State Farm")}


def _list_jobs_api(key, role):
    host, _ = JOBS_API_SITES[key]
    q = urllib.parse.quote(role)
    status, d = get_json(f"https://{host}/api/jobs?keywords={q}&limit=25")
    out = []
    for j in (d or {}).get("jobs") or []:
        j = j.get("data") or j
        url = f"https://{host}/main/jobs/{j.get('slug') or j.get('req_id')}"
        out.append({"board": "webpage", "title": j.get("title"), "url": url,
                    "location": ", ".join(x for x in (j.get("city"), j.get("state")) if x),
                    "fetch": {"board": "webpage", "url": url}})
    return out


AMAZON_SEARCH = "https://www.amazon.jobs/en/search.json"
AMAZON_NAMES = {"amazon", "aws", "amazonwebservices", "amazon-web-services", "amazonjobs"}


def _amazon_posting(j, company=None):
    body = (f"<h3>Description</h3>{j.get('description') or ''}"
            f"<h3>Basic qualifications</h3>{j.get('basic_qualifications') or ''}"
            f"<h3>Preferred qualifications</h3>{j.get('preferred_qualifications') or ''}")
    return _posting("amazon", company or j.get("company_name") or "Amazon", j.get("title"),
                    j.get("normalized_location") or j.get("location"), f"https://www.amazon.jobs{j.get('job_path', '')}",
                    html_to_lines(body))


def fetch_amazon(job_id, company=None):
    status, d = get_json(f"{AMAZON_SEARCH}?base_query={job_id}&result_limit=10")
    for j in (d or {}).get("jobs") or []:
        if str(j.get("id_icims")) == str(job_id):
            return _amazon_posting(j, company)
    raise BoardError("closed", f"Amazon job {job_id} is closed or does not exist")


def _list_amazon(role):
    q = urllib.parse.quote(role)
    status, d = get_json(f"{AMAZON_SEARCH}?base_query={q}&result_limit=25&sort=relevant")
    return [{"board": "amazon", "title": j.get("title"), "url": f"https://www.amazon.jobs{j.get('job_path', '')}",
             "location": j.get("normalized_location") or j.get("location"),
             "fetch": {"board": "amazon", "slug": "amazon", "job_id": str(j.get("id_icims"))}}
            for j in (d or {}).get("jobs") or []]


class _JsonLd(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.blocks = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "script" and (dict(attrs).get("type") or "").lower() == "application/ld+json":
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
        kinds = kind if isinstance(kind, list) else [kind]
        if any(str(k).endswith("JobPosting") for k in kinds):
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
            job = _find_jobposting(json.loads(block, strict=False))
        except (ValueError, RecursionError):
            continue
        if job:
            text = lambda x: x if isinstance(x, str) else " ".join(map(str, x)) if isinstance(x, list) else ""
            org = job.get("hiringOrganization")
            loc = job.get("jobLocation")
            if isinstance(loc, list):
                loc = loc[0] if loc else {}
            addr = loc.get("address") if isinstance(loc, dict) else None
            if isinstance(addr, dict):
                where = ", ".join(text(addr.get(k)) for k in ("addressLocality", "addressRegion") if text(addr.get(k)))
            else:
                where = text(addr)
            name = text(org.get("name")) if isinstance(org, dict) else text(org)
            body = html_to_lines(text(job.get("description")))
            if body:
                return _posting("webpage", company or name, text(job.get("title")), where, url, body)
    raise BoardError("unreachable", "that page has no machine-readable job description; read it with the web "
                                    "ability and pass the text with --text-file")


def fetch_link(link, company=None):
    """Fetch one posting from a parsed link (parse_link) or a search candidate's "fetch" dict."""
    board = link["board"]
    if link.get("job_id") is None and board in ("greenhouse", "lever", "ashby"):
        raise BoardError("board_only", f"that link is {pretty_company(link['slug'])}'s whole job board, not one posting")
    if board == "greenhouse":
        try:
            return fetch_greenhouse(link["slug"], link["job_id"], company)
        except BoardError:
            if not link.get("guessed"):
                raise
            return fetch_webpage(link["guessed"], company)  # the board name was a guess from the domain
    if board == "lever":
        return fetch_lever(link["slug"], link["job_id"], company)
    if board == "ashby":
        raw = link.get("raw")
        if raw:
            return _posting("ashby", company or pretty_company(link["slug"]), raw.get("title"), raw.get("location"),
                            raw.get("jobUrl"), html_to_lines(raw.get("descriptionHtml")) or text_to_lines(raw.get("descriptionPlain")))
        return fetch_ashby(link["slug"], link["job_id"], company)
    if board == "smartrecruiters":
        return fetch_smartrecruiters(link["slug"], link["job_id"], company)
    if board == "workday":
        return fetch_workday(link["slug"], link["wd"], link["site"], link["job_path"], company)
    if board == "amazon":
        return fetch_amazon(link["job_id"], company)
    if board == "eightfold":
        return fetch_eightfold(link["host"], link["domain"], link["job_id"], company)
    return fetch_webpage(link["url"], company)


def fetch_by_link(url, company=None):
    return fetch_link(parse_link(url), company)


# ---------------------------------------------------------------- search by company + role

ROLE_EXPANSIONS = {
    "swe": "software engineer", "sde": "software develop engineer", "pm": "product manager",
    "ml": "machine learning", "ai": "artificial intelligence", "ds": "data scientist", "internship": "intern",
    "interns": "intern", "co": "", "op": "intern", "coop": "intern", "eng": "engineer", "engineering": "engineer",
    "dev": "develop", "developer": "develop", "development": "develop", "jr": "junior", "sr": "senior",
}
ROLE_NOISE = {"summer", "fall", "spring", "winter", "the", "a", "an", "and", "or", "of", "for", "at", "role", "position",
              "remote", "hybrid", "onsite", "new", "grad"}
# Countries and their codes show up in titles ("SDE Intern (USA)", "... - Spain"); they name an office, not a role.
PLACES = {"us", "usa", "can", "canada", "uk", "gbr", "india", "ind", "zaf", "spain", "esp", "germany", "deu", "ireland",
          "irl", "israel", "isr", "poland", "pol", "mexico", "mex", "brazil", "bra", "singapore", "sgp", "japan", "jpn",
          "china", "chn", "prc", "taiwan", "twn", "korea", "kor", "australia", "aus", "france", "fra", "netherlands",
          "nld", "italy", "ita", "romania", "rou", "vietnam", "vnm", "philippines", "phl", "emea", "apac", "latam",
          "london", "dublin", "toronto", "vancouver", "bangalore", "bengaluru", "hyderabad", "seattle", "austin",
          "nyc", "sf", "bay", "area"}
ROLE_NOISE |= PLACES


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


def covers(role, title):
    """Every word of the role (after normalizing) appears in the title."""
    want, have = normalize_title(role), normalize_title(title)
    return bool(want) and all(w in have for w in want)


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
    # A one-word fallback ("capital" for Capital One) finds unrelated boards, so only exact names are tried.
    cands = ["".join(words), "-".join(words)]
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
             "fetch": {"board": "ashby", "slug": slug, "job_id": j.get("id"), "raw": j}} for j in d.get("jobs") or []]


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


def list_board(link, role):
    """Every posting on one company's board (from a board link), ranked for a role."""
    listers = {"greenhouse": _list_greenhouse, "lever": _list_lever, "ashby": _list_ashby}
    jobs = listers[link["board"]](link["slug"]) if link["board"] in listers else []
    if not jobs:
        raise BoardError("not_found", f"no open postings on {pretty_company(link['slug'])}'s {link['board']} board")
    return rank(jobs, role)


SEASON_YEAR = re.compile(r"\b(summer|fall|spring|winter|20\d\d)\b", re.I)


def rank(jobs, role, location=None, limit=5):
    want = {w.lower() for w in SEASON_YEAR.findall(role)}
    for j in jobs:
        j["score"] = title_score(role, j["title"])
        j["covers"] = covers(role, j["title"])
        have = {w.lower() for w in SEASON_YEAR.findall(j["title"] or "")}
        if want and have:  # "Summer 2027" should beat "Fall 2026"
            j["score"] = round(j["score"] + (0.03 if want <= have else -0.1), 3)
        if location and location_matches(location, j.get("location")):
            j["score"] = round(j["score"] + 0.05, 3)
    jobs.sort(key=lambda j: -j["score"])
    best = jobs[0]["title"]
    same_title = [j for j in jobs if same_role(j["title"], best)][:12]
    return same_title + [j for j in jobs if not same_role(j["title"], best)][:limit]


def search(company, role, location=None, limit=5):
    """Candidate postings for a company + role, best title match first."""
    jobs = []
    for slug in slug_candidates(company):
        if slug in AMAZON_NAMES:
            jobs += _list_amazon(role)
        if slug in WORKDAY_ALIASES:
            jobs += _list_workday(WORKDAY_ALIASES[slug], role)
        if slug in EIGHTFOLD_ALIASES:
            jobs += _list_eightfold(EIGHTFOLD_ALIASES[slug], role)
        if slug in JOBS_API_SITES:
            jobs += _list_jobs_api(slug, role)
        if not jobs:
            for lister in (_list_greenhouse, _list_lever, _list_ashby):
                jobs += lister(slug)
        if not jobs:
            jobs += _list_smartrecruiters(slug, role)
        if jobs:
            break
    if not jobs:
        raise BoardError("not_found", f"no public job board found for '{company}' (searched Greenhouse, Lever, Ashby, "
                                      "SmartRecruiters, Amazon, Microsoft, Lockheed Martin, State Farm and 11 Workday sites)")
    if INTERN.search(role):
        interns = [j for j in jobs if INTERN.search(j.get("title") or "")]
        if not interns:
            closest = rank(jobs, role, location, 3)[:3]
            raise BoardError("no_intern", f"{company} has no open intern posting matching '{role}' right now",
                             candidates=closest)
        jobs = interns
    ranked = rank(jobs, role, location, limit)
    if INTERN.search(role) and ranked[0]["score"] < 0.6:
        raise BoardError("no_intern", f"{company} has no open intern posting close to '{role}' right now",
                         candidates=ranked[:3])
    _workday_offices(ranked)
    return ranked


INTERN = re.compile(r"\b(intern|interns|internship|co-?op|apprentice\w*|summer analyst|student)\b", re.I)


def _workday_offices(cands):
    """Workday lists a posting with several offices as '5 Locations'; read the real ones for the top few."""
    for c in cands[:6]:
        f = c["fetch"]
        if f["board"] == "workday" and re.fullmatch(r"\d+ Locations", str(c.get("location") or "")):
            try:
                status, d = get_json(f"https://{f['slug']}.{f['wd']}.myworkdayjobs.com/wday/cxs/{f['slug']}/{f['site']}/{f['job_path']}")
                c["location"] = _workday_location((d or {}).get("jobPostingInfo") or {}) or c["location"]
            except BoardError:
                pass


US_STATE = re.compile(r"\b(US|USA|AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|"
                      r"NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b")
US_PLACE = re.compile(r"\b(united states|remote in the us|us remote|remote|seattle|san francisco|new york|nyc|chicago|"
                      r"austin|phoenix|tempe|chandler|scottsdale|boston|los angeles|denver|atlanta|dallas|"
                      r"washington|san jose|mountain view|palo alto|menlo park|redmond|portland|miami)\b", re.I)


STATES = {"alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
          "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
          "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
          "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
          "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
          "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
          "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
          "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
          "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
          "wisconsin": "WI", "wyoming": "WY"}


NON_US = re.compile(r"\b(canada|united kingdom|uk|gbr|england|scotland|ireland|india|germany|deutschland|france|spain|"
                    r"italy|netherlands|poland|romania|israel|china|prc|japan|korea|taiwan|singapore|australia|"
                    r"vietnam|philippines|malaysia|mexico|brazil|argentina|colombia|chile|south africa|zaf|"
                    r"berlin|munich|paris|amsterdam|dublin|london|toronto|vancouver|montreal|bengaluru|bangalore|"
                    r"hyderabad|pune|chennai|mumbai|delhi|noida|gurgaon|shanghai|beijing|shenzhen|tokyo|seoul|"
                    r"taipei|sydney|tel aviv|haifa|warsaw|gdansk|krakow|bucharest|madrid|barcelona|zurich|"
                    r"penang|kulim|manila|taguig|cape town|sao paulo|guadalajara)\b", re.I)


def is_us(location):
    text = str(location or "")
    if NON_US.search(text):
        return False
    return bool(US_STATE.search(text) or US_PLACE.search(text))


def location_matches(wanted, location):
    """'Arizona', 'AZ' and 'Phoenix' all match 'US, Arizona, Chandler' or 'Phoenix, AZ' as appropriate."""
    want, have = (wanted or "").strip().lower(), str(location or "")
    if not want or not have:
        return False
    if re.search(rf"\b{re.escape(want)}\b", have, re.I) and not (len(want) == 2 and want.upper() not in STATES.values()):
        return True
    code = STATES.get(want) or (want.upper() if want.upper() in STATES.values() else None)
    if code:
        name = next(n for n, c in STATES.items() if c == code)
        return bool(re.search(rf"\b{code}\b", have) or name in have.lower())
    return False


def choose(candidates, min_score=0.75, margin=0.06, location=None):
    """The clear winner among search candidates, or None when the student has to pick.

    Postings within `margin` of the best title score are contenders. If the student named a place and the contenders
    there are all the same role, that one wins. Otherwise several postings of one role (one per office) count as one
    match, and the one in the student's place, else a US one, is used; the caller says which office it picked.
    """
    if not candidates:
        return None
    top = candidates[0]
    if location:
        # A posting in the student's place whose title has every word of the role is a confident match.
        good = [c for c in candidates if c["score"] >= top["score"] - 0.15 and (c["score"] >= min_score or c.get("covers"))]
        local = [c for c in good if location_matches(location, c.get("location"))]
        if local and all(same_role(c["title"], local[0]["title"]) for c in local):
            return local[0]
    if top["score"] < min_score:
        return None
    contenders = [c for c in candidates if top["score"] - c["score"] < margin]
    same = [c for c in candidates if same_role(c["title"], top["title"])]
    if any(not same_role(c["title"], top["title"]) for c in contenders):
        return None
    if location:
        local = [c for c in same if location_matches(location, c.get("location"))]
        if local:
            return local[0]
    us = [c for c in same if is_us(c.get("location"))]
    if location and not us:
        return None
    return (us or same)[0]


def order_for_student(candidates, location=None):
    """Candidates to show the student: their place first, then US offices, then the rest."""
    return sorted(candidates, key=lambda c: (not location_matches(location, c.get("location")) if location else 0,
                                             not is_us(c.get("location")), -c.get("score", 0)))


def fetch_candidate(candidate, company=None):
    return fetch_link(candidate["fetch"], company)
