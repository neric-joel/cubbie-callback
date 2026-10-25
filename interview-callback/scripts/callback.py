"""interview-callback: prep a student for one interview from the real job posting.

Run from the Skill folder:
  python3 scripts/callback.py fetch --url <job link>                      # or --company X --role Y, or --text-file
  python3 scripts/callback.py brief [--id ID] [--when "Thu Oct 29, 2 PM Arizona"]
  python3 scripts/callback.py question [--id ID] [--tone chill|tough] [--term Kafka] [--retry]
  python3 scripts/callback.py score [--id ID] [--question-id Q] --answer-file answer.txt
  python3 scripts/callback.py invite --email-file invite.txt
  python3 scripts/callback.py list | forget --id ID | forget --all
Add --sample to any command to use the bundled example (no network, separate saved state).

Output is JSON. Exit codes: 0 ok, 1 unexpected error, 2 missing or bad input, 3 no posting found, 4 several possible
postings, interviews or questions, 5 job board unreachable, 6 posting closed and no saved copy, 7 no saved interview,
8 every question asked, 9 another command is still running.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent))
import boards  # noqa: E402
import coach  # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "assets"
EXIT = {"error": 1, "bad_input": 2, "board_only": 2, "not_found": 3, "no_intern": 3, "ambiguous": 4,
        "unreachable": 5, "closed": 6, "no_interview": 7, "done": 8, "busy": 9}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
LIMIT = 7800  # Cubbie reads the first 8,000 characters of output


class Stop(Exception):
    def __init__(self, kind, message, nxt, **extra):
        super().__init__(message)
        self.kind, self.message, self.next, self.extra = kind, message, nxt, extra


class Help(Exception):
    """--help, answered as JSON like everything else."""


def _shrink(x, chars, items):
    if isinstance(x, str):
        return x[:chars] + "..." if len(x) > chars else x
    if isinstance(x, list):
        more = [f"... {len(x) - items} more"] if len(x) > items else []
        return [_shrink(i, chars, items) for i in x[:items]] + more
    if isinstance(x, dict):
        return {k: _shrink(v, chars, items) for k, v in x.items()}
    return x


def emit(data: dict, code: int = 0) -> NoReturn:
    """Print JSON that always parses and fits: long strings and lists are shortened, `next` survives."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    text = json.dumps(data, ensure_ascii=False, indent=1)
    for chars, items in ((400, 12), (200, 6), (100, 3), (60, 2)):
        if len(text) <= LIMIT:
            break
        keep = data.get("next")
        data = dict(_shrink(data, chars, items), shortened=True)
        if keep:
            data["next"] = keep
        text = json.dumps(data, ensure_ascii=False, indent=1)
    print(text)
    sys.exit(code)


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_file(path: str, what: str) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read(2_000_000).decode("utf-8", errors="replace")
    except OSError:
        raise Stop("bad_input", f"can't read the {what} file '{path}'", f"Save the {what} to a file and pass its path.")


# ---------------------------------------------------------------- saved state (under HOME, survives Skill updates)

def state_dir(sample: bool) -> Path:
    root = Path(os.environ.get("INTERVIEW_CALLBACK_HOME") or Path.home() / ".config" / "interview-callback")
    d = root / "sample" if sample else root
    (d / "interviews").mkdir(parents=True, exist_ok=True)
    return d


def _migrate(rec):
    """Older records kept one waiting question in pending_question; now any number wait, in order, in pending."""
    old = rec.pop("pending_question", None)
    pending = rec.setdefault("pending", [])
    if old and old not in pending:
        pending.append(old)
    return rec


def all_records(sd: Path) -> list[dict]:
    recs = []
    for f in (sd / "interviews").glob("*.json"):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if isinstance(rec, dict) and isinstance(rec.get("posting"), dict) and rec.get("id"):
            recs.append(_migrate(rec))
    return sorted(recs, key=lambda r: (r.get("updated_ts", 0), r.get("updated_at", "")), reverse=True)


def record_path(sd: Path, rid: str | None) -> Path:
    if not ID_RE.fullmatch(rid or ""):
        raise Stop("bad_input", f"'{rid}' is not an interview id", "Run list and use one of its ids.")
    return sd / "interviews" / f"{rid}.json"


def load(sd: Path, rid: str | None) -> dict:
    if rid:
        f = record_path(sd, rid)
        if not f.exists():
            raise Stop("no_interview", f"no saved interview '{rid}'", "Run list to see saved interviews.")
        return _migrate(json.loads(f.read_text(encoding="utf-8")))
    recs = all_records(sd)
    if not recs:
        raise Stop("no_interview", "no saved interview yet", "Run fetch first with the job link or company and role.")
    return recs[0]


_LAST_TS = [0.0]


def save(sd: Path, rec: dict) -> None:
    rec["updated_at"] = now()
    # strictly increasing, so "the most recent interview" never ties within one clock tick
    newest = max([r.get("updated_ts", 0) for r in all_records(sd)] + [_LAST_TS[0]])
    rec["updated_ts"] = _LAST_TS[0] = max(time.time(), newest + 1e-6)
    f = record_path(sd, rec["id"])
    tmp = f.with_name(f".{f.stem}.{os.getpid()}.tmp")
    # errors="replace": a stray surrogate from a job board must never stop the save
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8", errors="replace")
    os.replace(tmp, f)  # never leave a half-written record behind


@contextmanager
def locked(sd: Path) -> Iterator[None]:
    """One command at a time per state folder, so two runs can't overwrite each other's changes."""
    fh = open(sd / ".lock", "a+")
    try:
        for _ in range(100):  # about 10 seconds
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise Stop("busy", "another interview-callback command is still running", "Try again in a few seconds.")
        yield
    finally:
        fh.close()


def find_existing(sd: Path, posting: dict | None, link: str | None = None) -> dict | None:
    for r in all_records(sd):
        p = r["posting"]
        if (link and r.get("link") == link) or \
                (posting and p.get("source_url") and p["source_url"] == posting.get("source_url")):
            return r
    return None


def new_id(sd: Path, posting: dict) -> str:
    base = f"{coach.slug(posting['company'], 20)}-{coach.slug(posting['title'], 30)}"
    rid, n = base, 2
    while (sd / "interviews" / f"{rid}.json").exists():
        rid, n = f"{base}-{n}", n + 1
    return rid


def build_record(sd: Path, posting: dict, link: str | None, when: str | None, existing: dict | None = None,
                 place: str | None = None) -> dict:
    rec = existing or {"id": new_id(sd, posting), "created_at": now(), "asked": [], "questions": {}, "scores": [],
                       "pending": []}
    place = place or rec.get("place")
    prefer = (lambda line: boards.location_matches(place, line)) if place else None
    rec.update({"link": link or rec.get("link"), "posting": posting,
                "skills": coach.extract(posting["lines"], posting["company"]),
                "facts": coach.facts(posting["lines"], prefer),
                "keywords": coach.posting_keywords(posting["lines"], posting["company"], title=posting["title"])})
    if place:
        rec["place"] = place
    rec.pop("closed_at", None)
    if when:
        rec["when"] = when
    return rec


def summary(rec: dict, status: str) -> dict:
    p = rec["posting"]
    top = [{"term": s["term"], "kind": s["kind"], "mentions": s["mentions"], "line": s["line"]}
           for s in rec["skills"] if s["kind"] != "nice"][:5]
    out = {"ok": True, "status": status, "id": rec["id"], "company": p["company"], "title": p["title"],
           "location": p["location"], "board": p["board"], "source_url": p["source_url"], "lines": len(p["lines"]),
           "headline": coach.headline(rec), "top_skills": top,
           "next": "Run brief, then offer prep time and a mock interview."}
    if not rec.get("when"):
        out["next"] = "Ask the student once when the interview is (pass it with --when), then run brief."
    return out


# ---------------------------------------------------------------- fetch

NEXT = {
    "bad_input": "Ask the student for the job link, or the company and exact role title.",
    "not_found": "Search the web for the posting and run fetch --url with it, or ask the student for the link.",
    "ambiguous": "Send the student up to three candidate links (each on its own line) and ask which; then run "
                 "fetch --url.",
    "unreachable": "Read the job page with the web ability, save the text to a file, and run fetch --text-file.",
    "closed": "The posting is gone. Ask the student to paste the description or send a screenshot, then use "
              "--text-file.",
    "board_only": "Ask which role they're interviewing for, then run fetch --url <same link> --role \"<role>\".",
    "no_intern": "Tell the student there's no open intern posting like that right now (postings often close once "
                 "interviews start). Ask for the link from their application or a screenshot of the posting, or "
                 "offer the closest posting below.",
    "busy": "Try again in a few seconds.",
}


def _ambiguous(company, role, cands, location=None):
    us = [c for c in cands if boards.is_us(c.get("location"))]
    shown = boards.order_for_student(us or cands, location)[:6]
    return Stop("ambiguous", f"several {company} postings could match '{role}'", NEXT["ambiguous"],
                candidates=[{"n": i + 1, "title": c["title"], "location": c.get("location"), "url": c["url"],
                             "match": c["score"]} for i, c in enumerate(shown)])


def _location_note(place, pick, posting):
    """What to tell the student when the posting's listed office isn't the place they named."""
    if boards.location_matches(place, pick.get("location")) or boards.location_matches(place, posting.get("location")):
        return None
    shown = place[:1].upper() + place[1:]
    n = boards.place_line(place, posting["lines"])
    if n:
        return f"{shown} is one of this posting's locations (line {n})."
    where = pick.get("location") or "an unlisted place"
    multi = boards.multi_site_line(posting["lines"])
    if multi:
        return (f"This posting lists {where}, but it says it covers several US sites (line {multi}); ask the "
                f"recruiter whether {shown} is one of them.")
    return f"No {shown} posting for this role; this one is in {where}."


def _from_candidates(sd, a, cands, company):
    pick = boards.choose(cands, location=a.location)
    if not pick and len(cands) == 1 and cands[0].get("relevant"):
        pick = cands[0]  # one posting matched: use it (the student is told which, and can correct it)
    if not pick:
        raise _ambiguous(company, a.role, cands, a.location)
    try:
        posting = boards.fetch_candidate(pick, a.company)
    except boards.BoardError as e:
        raise Stop(e.kind, e.message, NEXT.get(e.kind, ""))
    rec = build_record(sd, posting, pick["url"], a.when, find_existing(sd, posting), a.location)
    save(sd, rec)
    out = summary(rec, "matched")
    out["matched_title"], out["match"] = pick["title"], pick["score"]
    others = sorted({str(c.get("location")) for c in cands if boards.same_role(c["title"], pick["title"])
                     and c is not pick and c.get("location") != pick.get("location")
                     and (boards.is_us(c.get("location")) or not boards.is_us(pick.get("location")))})
    if others:
        out["other_offices"] = others[:8]
    note = _location_note(a.location, pick, posting) if a.location else None
    if not a.location and pick.get("location") and not boards.is_us(pick.get("location")):
        note = (f"This posting is in {pick['location']}. If the interview is for another office, ask for that "
                "posting's link.")
    if note:
        out["location_note"] = note
    out["next"] = ("Tell the student which posting you used (office, and the link on its own line) so they can correct "
                   "it. " + out["next"])
    return out


def _fetch_sample(a, sd):
    d = json.loads((ASSETS / "sample_posting.json").read_text(encoding="utf-8"))
    posting = boards.posting_from_greenhouse(d, "saguaropay")
    rec = build_record(sd, posting, posting["source_url"], a.when or "Thu Oct 29, 2:00 PM (sample)",
                       find_existing(sd, posting))
    save(sd, rec)
    return summary(rec, "fetched")


def _clean_link(url):
    try:
        return boards.clean_url(url) if url else url
    except boards.BoardError:
        return url


def _fetch_text(a, sd):
    if not (a.company and a.role):
        raise Stop("bad_input", "--text-file needs --company and --role", "Ask the student for the company and role.")
    lines = boards.text_to_lines(read_file(a.text_file, "posting text"))
    if len(lines) < 3:
        raise Stop("bad_input", "the posting text is nearly empty", "Read the job page again or ask for a screenshot.")
    source = _clean_link(a.source_url)
    try:
        posting = boards._posting("pasted", a.company, a.role, a.location, source or "", lines)
    except boards.BoardError as e:
        raise Stop("bad_input", e.message, "Read the job page again or ask for a screenshot.")
    rec = build_record(sd, posting, source, a.when, find_existing(sd, posting, source), a.location)
    save(sd, rec)
    return summary(rec, "from_text")


def _fetch_url(a, sd):
    try:
        link, url = boards.parse_link(a.url), boards.clean_url(a.url)  # a user:password@ part is never saved
    except boards.BoardError as e:
        raise Stop(e.kind, e.message, NEXT[e.kind])
    if link["board"] in ("greenhouse", "lever", "ashby") and link.get("job_id") is None:
        if not a.role:
            raise Stop("bad_input", f"that link is {boards.pretty_company(link['slug'])}'s whole job board, "
                                    "not one posting", NEXT["board_only"])
        try:
            cands = boards.list_board(link, a.role)
        except boards.BoardError as e:
            raise Stop(e.kind, e.message, NEXT.get(e.kind, ""))
        return _from_candidates(sd, a, cands, a.company or boards.pretty_company(link["slug"]))
    existing = find_existing(sd, None, url)
    try:
        posting = boards.fetch_link(link, a.company)
    except boards.BoardError as e:
        if e.kind == "closed" and existing:
            existing["closed_at"] = existing.get("closed_at") or now()
            save(sd, existing)
            out = summary(existing, "closed_using_saved_copy")
            out["note"] = f"The posting is closed now; using the copy saved {existing['posting']['fetched_at'][:10]}."
            return out
        raise Stop(e.kind, e.message, NEXT.get(e.kind, ""))
    rec = build_record(sd, posting, url, a.when, existing or find_existing(sd, posting))
    save(sd, rec)
    return summary(rec, "fetched")


def _fetch_search(a, sd):
    try:
        cands = boards.search(a.company, a.role, a.location)
    except boards.BoardError as e:
        extra = {"closest": [{"title": c["title"], "location": c.get("location"), "url": c["url"]}
                             for c in e.candidates]} if e.candidates else {}
        raise Stop(e.kind, e.message, NEXT.get(e.kind, ""), **extra)
    return _from_candidates(sd, a, cands, a.company)


def cmd_fetch(a: argparse.Namespace, sd: Path) -> dict:
    if a.sample:
        return _fetch_sample(a, sd)
    if a.text_file:
        return _fetch_text(a, sd)
    if a.url:
        return _fetch_url(a, sd)
    if a.company and a.role:
        return _fetch_search(a, sd)
    raise Stop("bad_input", "give --url, or --company and --role, or --text-file", "Ask the student for the job link.")


def cmd_brief(a: argparse.Namespace, sd: Path) -> dict:
    rec = load(sd, a.id)
    if a.when:
        rec["when"] = a.when
    out_path = sd / "briefs" / f"{rec['id']}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(coach.brief_markdown(rec), encoding="utf-8", errors="replace")
    save(sd, rec)
    out = {"ok": True, "id": rec["id"], "brief_path": str(out_path), "headline": coach.headline(rec),
           "texts": coach.brief_texts(rec), "ask_them": coach.ask_them(rec),
           "next": "Turn brief_path into a PDF or Google Doc (the brief first, the numbered posting after) and send "
                   "it after these texts."}
    options = coach.language_options(rec["skills"])
    if options:
        out["language_options"] = options
        out["next"] += (" The posting accepts any one of " + ", ".join(options) + ": ask the student once which "
                        "they'll interview in, then use question --term <that language>.")
    return out


# ---------------------------------------------------------------- questions

def _weak(row):
    """An answer worth redoing: points missed, only key words, or hedged."""
    return row["covered"] < row["total"] or row.get("verdict") == "key words only" or bool(row.get("hedged"))


def _best(rows):
    """The best of these score rows (a later one wins a tie), or None."""
    best = None
    for s in rows:
        key = (s["covered"] / (s["total"] or 1), not _weak(s))
        if best is None or key >= (best["covered"] / (best["total"] or 1), not _weak(best)):
            best = s
    return best


def _best_scores(rec):
    by_question = {}
    for s in rec.get("scores", []):
        by_question.setdefault(s["question_id"], []).append(s)
    return {qid: _best(rows) for qid, rows in by_question.items()}


def _stop_waiting(rec, qid):
    if qid in rec.setdefault("pending", []):
        rec["pending"].remove(qid)


def cmd_question(a: argparse.Namespace, sd: Path) -> dict:
    rec = load(sd, a.id)
    tone = a.tone or rec.get("tone") or "chill"
    retry_of = None
    if a.retry:
        best = _best_scores(rec)
        if a.question_id:
            qid = a.question_id
        else:
            weak = sorted((s for s in best.values() if _weak(s)), key=lambda s: s["covered"] / (s["total"] or 1))
            if not weak:
                raise Stop("done", "every answered question already covered all its points",
                           "Offer a new question (run question without --retry) or wrap up.")
            qid = weak[0]["question_id"]
        q = coach.next_question(dict(rec, asked=[]), tone, qid)
        retry_of = best.get(qid)
    else:
        q = coach.next_question(rec, tone, a.question_id, a.term)
    if not q and (a.term or a.question_id):
        known = sorted({x["term"] for x in coach.plan_questions(rec) if x["term"]})
        raise Stop("bad_input", f"no unasked question for '{a.term or a.question_id}'",
                   "Use --retry to redo a weak answer, or pick one of these skills: " + ", ".join(known))
    if not q:
        raise Stop("done", "every planned question has been asked",
                   "Run question --retry to redo the weakest answer, or wrap up.")
    rec.setdefault("asked", [])
    if q["id"] not in rec["asked"]:
        rec["asked"].append(q["id"])
    rec.setdefault("questions", {})[q["id"]] = {k: q.get(k) for k in ("id", "question", "term", "line", "quote",
                                                                      "points", "wrong")}
    follow = rec.pop("follow_up", None)  # a new question (or a redo) replaces any open follow-up
    if follow and follow["question_id"] != q["id"]:
        _stop_waiting(rec, follow["question_id"])  # it only waited for that follow-up
    _stop_waiting(rec, q["id"])
    rec["pending"].append(q["id"])
    rec["tone"] = tone
    save(sd, rec)
    out = {"ok": True, "id": rec["id"], "question_id": q["id"], "question": q["question"], "term": q["term"],
           "why": q["why"], "tone": tone, "call_script": q["call_script"], "text_version": q["text_version"],
           "listening_for": [p["label"] for p in q["points"]],
           "score_command": f"python3 scripts/callback.py score --id {rec['id']} --question-id {q['id']} "
                            "--answer-file <file>",
           "next": "Ask it now by text, or put call_script and text_version word for word into a scheduled call. "
                   "When the answer arrives, run score_command."}
    if retry_of:
        out["retry_of"] = f"{retry_of['covered']}/{retry_of['total']}"
    return out


# ---------------------------------------------------------------- scoring

def _waiting_text(rec, qid):
    follow = rec.get("follow_up") or {}
    if follow.get("question_id") == qid and follow.get("text"):
        return follow["text"]
    return ((rec.get("questions") or {}).get(qid) or {}).get("question")


def _answer_target(a, sd):
    """The interview and question an answer (or a --credit) belongs to."""
    if a.credit:
        # a credit belongs to the answer just scored, wherever other questions are still waiting
        if a.id:
            rec = load(sd, a.id)
        else:
            scored = [r for r in all_records(sd) if r.get("scores")]
            rec = max(scored, key=lambda r: r["scores"][-1]["at"]) if scored else load(sd, None)
        qid = a.question_id or rec.get("last_question")
    else:
        waiting = [] if a.id else [r for r in all_records(sd) if r.get("pending")]
        if a.question_id and len(waiting) > 1:
            waiting = [r for r in waiting if a.question_id in r["pending"]] or waiting
        if len(waiting) > 1:
            raise Stop("ambiguous", "several interviews are waiting for an answer",
                       "Ask the student which one this answer is for, then pass --id.",
                       candidates=[{"id": r["id"], "company": r["posting"]["company"],
                                    "title": coach.spoken_title(r["posting"]["title"]),
                                    "question": _waiting_text(r, r["pending"][0])} for r in waiting[:4]])
        rec = waiting[0] if waiting else load(sd, a.id)
        pending = rec.get("pending") or []
        if not a.question_id and len(pending) > 1:
            raise Stop("ambiguous", "several questions are waiting for an answer",
                       "Match the answer to the waiting question it is about and run score again with "
                       "--question-id; if you can't tell, ask the student which question it answers.",
                       id=rec["id"], candidates=[{"question_id": qid, "question": _waiting_text(rec, qid)}
                                                 for qid in pending[:6]])
        qid = a.question_id or (pending[0] if pending else None)
    q = (rec.get("questions") or {}).get(qid or "")
    if not q:
        raise Stop("bad_input", "no question is waiting for an answer",
                   "Run question first, or pass --question-id (list shows the interview's questions).")
    return rec, qid, q


def _read_answer(a):
    if a.answer_file:
        answer = read_file(a.answer_file, "answer")
    elif a.answer:
        answer = a.answer
    elif a.sample:
        answer = (ASSETS / "sample_answer.txt").read_text(encoding="utf-8")
    else:
        raise Stop("bad_input", "give --answer-file or --answer", "Transcribe the voice note to a file and pass it.")
    if len(answer.split()) < 5:
        raise Stop("bad_input", "the answer is only a few words", "Ask the student to answer again in a voice note.")
    return answer


def _follow_up_text(q, label):
    """The tough interviewer's follow-up on a missed point: the point's own question, or a neutral one."""
    point = next((p for p in q["points"] if p["label"] == label), {})
    return "Follow-up: " + (point.get("follow_up") or
                            f"one part your answer didn't get to is \"{label}\". Can you add that now?")


def _head(covered, total, verdict, before=None):
    if before:
        return f"{before['covered']}/{before['total']} last time, {covered}/{total} now: {verdict}."
    if verdict == "key words only":
        return f"{covered}/{total} key points, but only as key words. Say how and why, in about a minute."
    return f"{covered}/{total} key points: {verdict}."


def _card(q, head, missed, tips, follow_text=None):
    """The scorecard texts: verdict, covered, missed (with the posting line behind it), tips, then the follow-up."""
    hit = [p["label"] for p in q["points"] if p["label"] not in missed]
    texts = [head]
    if hit:
        texts.append("Covered: " + "; ".join(hit) + ".")
    if missed:
        cited = next((p["cite"] for p in q["points"] if p["label"] in missed and p.get("cite")), None)
        if cited:
            cite = f" The posting says \"{cited['quote']}\" (line {cited['line']})."
        elif q.get("term") and q.get("line"):
            cite = f" The posting asks for {q['term']} on line {q['line']}."
        else:
            cite = ""
        texts.append("Missed: " + "; ".join(missed) + "." + cite)
    texts += list(tips)[:2]
    if follow_text:
        texts.append(follow_text)  # last, so the student answers it
    return texts


def _score_full(rec, qid, q, answer):
    before = _best_scores(rec).get(qid)
    result = coach.score_answer(q, answer)
    rec.setdefault("scores", []).append({"at": now(), "question_id": qid, "covered": result["covered"],
                                         "total": result["total"], "verdict": result["verdict"],
                                         "missed": result["missed"], "words": result["words"],
                                         "hedged": result["hedged"], "list_like": result["list_like"],
                                         "tips": result["tips"][:2]})
    if before:
        result["progress"] = f"{before['covered']}/{before['total']} -> {result['covered']}/{result['total']}"
    follow_text = None
    if rec.get("tone") == "tough" and result["missed"]:
        old = rec.get("follow_up")
        if old and old["question_id"] != qid:
            _stop_waiting(rec, old["question_id"])  # one open follow-up at a time; the older one is dropped
        follow_text = result["follow_up"] = _follow_up_text(q, result["missed"][0])
        rec["follow_up"] = {"question_id": qid, "points": result["missed"][:1], "text": follow_text}
    head = _head(result["covered"], result["total"], result["verdict"], before)
    return result, _card(q, head, result["missed"], result["tips"], follow_text)


def _score_follow_up(rec, qid, q, answer, follow):
    """The reply to a follow-up grades only the point it asked about, and is credited "after the hint"."""
    part = dict(q, points=[p for p in q["points"] if p["label"] in follow["points"]])
    result = coach.score_answer(part, answer)
    if result["list_like"]:  # a string of key words doesn't answer the follow-up
        for p in result["points"]:
            p.update(covered=False, evidence=None)
        result["covered"], result["missed"] = 0, [p["point"] for p in result["points"]]
    result["follow_up_of"] = qid
    tips = result["misconceptions"] + [t for t in result["tips"] if result["list_like"] and t.startswith("That reads")]
    result["tips"] = tips
    row = next((s for s in reversed(rec.get("scores", [])) if s["question_id"] == qid), None)
    got = [p["point"] for p in result["points"] if p["covered"]]
    if row and got:
        row["after_hint"] = sorted(set(row.get("after_hint", [])) | {g for g in got if g in row["missed"]})
    if result["missed"]:
        head = "Follow-up: not quite."
    elif row:
        head = (f"Follow-up: got it. That's {row['covered']}/{row['total']} on your own, "
                f"+{len(row.get('after_hint', []))} after the hint. A redo without the hint is the real test.")
    else:
        head = "Follow-up: got it."
    rec.pop("follow_up", None)
    return result, _card(part, head, result["missed"], tips)


def cmd_score(a: argparse.Namespace, sd: Path) -> dict:
    rec, qid, q = _answer_target(a, sd)
    if a.credit:
        return _credit(sd, rec, qid, a.credit)
    answer = _read_answer(a)
    follow = rec.get("follow_up") if (rec.get("follow_up") or {}).get("question_id") == qid else None
    if follow:
        result, texts = _score_follow_up(rec, qid, q, answer, follow)
    else:
        result, texts = _score_full(rec, qid, q, answer)
    _stop_waiting(rec, qid)  # other waiting questions keep waiting
    asks_follow_up = (rec.get("follow_up") or {}).get("question_id") == qid
    if asks_follow_up:
        rec["pending"].append(qid)  # now it waits for the reply to the follow-up
    rec["last_question"] = qid
    save(sd, rec)
    nxt = ("Send the texts one by one. Then offer a redo of this question (question --retry) or the next one "
           "(question), each as a scheduled check-in at a time the student picks.")
    if asks_follow_up:
        nxt = ("Send the texts one by one; the last is a follow-up question. Wait for the student's reply, then run "
               "score again with it (it grades only the follow-up).")
    if rec["pending"]:
        nxt += " Still waiting for an answer: " + ", ".join(rec["pending"]) + "."
    result.update({"ok": True, "id": rec["id"], "texts": texts, "next": nxt})
    return result


def _credit(sd, rec, qid, label):
    """Cubbie judged that the answer made a point in other words: record it, so redos and weak spots agree, and
    return the corrected scorecard to send instead of the first one."""
    rows = [s for s in rec.get("scores", []) if s["question_id"] == qid]
    last = rows[-1] if rows else None
    match = next((m for m in (last or {}).get("missed", []) if label.lower() in m.lower()), None)
    if not match:
        raise Stop("bad_input", f"'{label}' isn't a missed point of the last answer to {qid}",
                   "Pass part of a missed point's label exactly as score printed it.")
    q = (rec.get("questions") or {}).get(qid) or {"points": []}
    core = next((p["label"] for p in q["points"] if p.get("core")), q["points"][0]["label"] if q["points"] else None)
    last["missed"].remove(match)
    last["covered"] += 1
    last["credited"] = last.get("credited", []) + [match]
    last["verdict"] = coach.verdict_for(last["covered"], last["total"], last.get("words"), bool(last.get("hedged")),
                                        bool(last.get("list_like")), core in last["missed"])
    follow, follow_text = rec.get("follow_up"), None
    if follow and follow.get("question_id") == qid:
        follow["points"] = [p for p in follow["points"] if p != match]
        if not follow["points"] and last["missed"]:  # ask about the next missed point instead
            follow.update(points=last["missed"][:1], text=_follow_up_text(q, last["missed"][0]))
        if follow["points"]:
            follow_text = follow.get("text") or _follow_up_text(q, follow["points"][0])
        else:
            rec.pop("follow_up")
            _stop_waiting(rec, qid)
    head = _head(last["covered"], last["total"], last["verdict"], _best(rows[:-1]))
    save(sd, rec)
    return {"ok": True, "id": rec["id"], "question_id": qid, "credited": match,
            "score": f"{last['covered']}/{last['total']}", "verdict": last["verdict"],
            "texts": _card(q, head, last["missed"], last.get("tips", []), follow_text),
            "next": "Send these texts instead of the ones score printed, one by one."}


# ---------------------------------------------------------------- the rest

def cmd_invite(a: argparse.Namespace, sd: Path) -> dict:
    if a.email_file:
        text = read_file(a.email_file, "email")
    elif a.sample:
        text = (ASSETS / "sample_invite.txt").read_text(encoding="utf-8")
    else:
        raise Stop("bad_input", "give --email-file", "Save the email text to a file and pass it.")
    out = coach.classify_email(text, boards.parse_link)
    out["ok"] = True
    return out


def _weak_spots(r):
    spots = set()
    for s in {x["question_id"]: x for x in r.get("scores", [])}.values():  # each question's latest answer
        spots |= set(s["missed"])
        name = ((r.get("questions") or {}).get(s["question_id"]) or {}).get("term") or s["question_id"]
        if s.get("verdict") == "key words only":
            spots.add(f"{name} answer: only key words, explain how and why")
        elif s.get("hedged"):
            spots.add(f"{name} answer: hedged, commit to what you'd do")
    return sorted(spots)[:5]


def cmd_list(a: argparse.Namespace, sd: Path) -> dict:
    items = []
    for r in all_records(sd):
        last = (r.get("scores") or [None])[-1]
        pending = r.get("pending") or []
        items.append({"id": r["id"], "company": r["posting"]["company"],
                      "title": coach.spoken_title(r["posting"]["title"]), "when": r.get("when"),
                      "questions_asked": len(r.get("asked", [])),
                      "waiting_for_answer": pending[0] if pending else None, "waiting": pending,
                      "last_score": f"{last['covered']}/{last['total']}" if last else None,
                      "questions": [{"id": qid, "term": (r.get("questions") or {}).get(qid, {}).get("term")}
                                    for qid in r.get("asked", [])],
                      "weak_spots": _weak_spots(r), "ask_them": coach.ask_them(r)})
    return {"ok": True, "interviews": items}


def cmd_forget(a: argparse.Namespace, sd: Path) -> dict:
    if a.all:
        files = list((sd / "interviews").glob("*.json")) + list((sd / "briefs").glob("*.md"))
        for f in files:
            f.unlink()
        n = sum(1 for f in files if f.suffix == ".json")
        if not a.sample:
            sample = sd / "sample"
            for f in list((sample / "interviews").glob("*.json")) + list((sample / "briefs").glob("*.md")):
                f.unlink()
        return {"ok": True, "deleted": f"all saved interviews ({n})"}
    if not a.id:
        raise Stop("bad_input", "forget needs --id or --all",
                   "Run list, confirm with the student which interview to delete, then pass its id.")
    rec = load(sd, a.id)
    record_path(sd, rec["id"]).unlink()
    brief = sd / "briefs" / f"{rec['id']}.md"
    if brief.exists():
        brief.unlink()
    return {"ok": True, "deleted": rec["id"]}


def _out_of_time():
    """Last resort before the sandbox's 2-minute limit: say so in JSON and stop."""
    sys.stdout.write(json.dumps({"ok": False, "error": "unreachable", "message": "ran out of time",
                                 "next": NEXT["unreachable"]}) + "\n")
    sys.stdout.flush()
    os._exit(5)


class JsonArgs(argparse.ArgumentParser):
    def error(self, message):
        raise Stop("bad_input", f"bad command: {message}", "Check the command against SKILL.md and run it again.")

    def print_help(self, file=None):
        raise Help(self.format_help())


def _parser():
    common = JsonArgs(add_help=False)
    common.add_argument("--sample", action="store_true", help="use the bundled example; no network; separate state")
    common.add_argument("--id", help="saved interview id (default: the most recent)")
    ap = JsonArgs(prog="callback.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", parents=[common])
    f.add_argument("--url")
    f.add_argument("--company")
    f.add_argument("--role")
    f.add_argument("--location")
    f.add_argument("--text-file")
    f.add_argument("--source-url")
    f.add_argument("--when", help="interview time as the student should see it")
    b = sub.add_parser("brief", parents=[common])
    b.add_argument("--when")
    q = sub.add_parser("question", parents=[common])
    q.add_argument("--tone", choices=["chill", "tough"])
    q.add_argument("--term")
    q.add_argument("--question-id")
    q.add_argument("--retry", action="store_true", help="re-ask the weakest answered question")
    s = sub.add_parser("score", parents=[common])
    s.add_argument("--answer-file")
    s.add_argument("--answer")
    s.add_argument("--question-id")
    s.add_argument("--credit", help="count a missed point the answer made in other words (part of its label)")
    i = sub.add_parser("invite", parents=[common])
    i.add_argument("--email-file")
    sub.add_parser("list", parents=[common])
    fg = sub.add_parser("forget", parents=[common])
    fg.add_argument("--all", action="store_true", help="delete every saved interview")
    return ap


HANDLERS = {"fetch": cmd_fetch, "brief": cmd_brief, "question": cmd_question, "score": cmd_score,
            "invite": cmd_invite, "list": cmd_list, "forget": cmd_forget}


def main(argv: list[str] | None = None) -> None:
    boards.DEADLINE = time.monotonic() + 100  # stay well inside the sandbox's 2-minute limit
    argv = [str(x).encode("utf-8", "replace").decode("utf-8") for x in (sys.argv[1:] if argv is None else argv)]
    watchdog = threading.Timer(110, _out_of_time)
    watchdog.daemon = True
    watchdog.start()
    try:
        try:
            a = _parser().parse_args(argv)
        except Help as h:
            emit({"ok": True, "usage": str(h), "next": "Run one of the commands shown."})
        except Stop as e:
            emit({"ok": False, "error": e.kind, "message": e.message, "next": e.next}, EXIT[e.kind])
        try:
            sd = state_dir(a.sample)
            with locked(sd):
                result = HANDLERS[a.cmd](a, sd)
            emit(result)
        except Stop as e:
            emit({"ok": False, "error": e.kind, "message": e.message, "next": e.next, **e.extra}, EXIT.get(e.kind, 1))
        except boards.BoardError as e:
            emit({"ok": False, "error": e.kind, "message": e.message, "next": NEXT.get(e.kind, "")},
                 EXIT.get(e.kind, 1))
        except Exception as e:  # never hand Cubbie a traceback
            emit({"ok": False, "error": "error", "message": f"{type(e).__name__}: {e}",
                  "where": traceback.format_exc(limit=3).splitlines()[-3:],
                  "next": "Tell the student the prep tool hit an error and keep helping from the posting text."}, 1)
    finally:
        watchdog.cancel()


if __name__ == "__main__":
    main()
