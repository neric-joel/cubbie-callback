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
postings, 5 job board unreachable, 6 posting closed and no saved copy, 7 no saved interview, 8 every question asked.
"""

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import boards  # noqa: E402
import coach  # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "assets"
EXIT = {"error": 1, "busy": 1, "bad_input": 2, "board_only": 2, "not_found": 3, "no_intern": 3, "ambiguous": 4, "unreachable": 5, "closed": 6,
        "no_interview": 7, "done": 8}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
LIMIT = 7800  # Cubbie reads the first 8,000 characters of output


class Stop(Exception):
    def __init__(self, kind, message, nxt, **extra):
        super().__init__(message)
        self.kind, self.message, self.next, self.extra = kind, message, nxt, extra


def _shrink(x, chars, items):
    if isinstance(x, str):
        return x[:chars] + "..." if len(x) > chars else x
    if isinstance(x, list):
        return [_shrink(i, chars, items) for i in x[:items]] + ([f"... {len(x) - items} more"] if len(x) > items else [])
    if isinstance(x, dict):
        return {k: _shrink(v, chars, items) for k, v in x.items()}
    return x


def emit(data, code=0):
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


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_file(path, what):
    try:
        with open(path, "rb") as fh:
            return fh.read(2_000_000).decode("utf-8", errors="replace")
    except OSError:
        raise Stop("bad_input", f"can't read the {what} file '{path}'", f"Save the {what} to a file and pass its path.")


# ---------------------------------------------------------------- saved state (under HOME, survives Skill updates)

def state_dir(sample):
    root = Path(os.environ.get("INTERVIEW_CALLBACK_HOME") or Path.home() / ".config" / "interview-callback")
    d = root / "sample" if sample else root
    (d / "interviews").mkdir(parents=True, exist_ok=True)
    return d


def all_records(sd):
    recs = []
    for f in (sd / "interviews").glob("*.json"):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if isinstance(rec, dict) and isinstance(rec.get("posting"), dict) and rec.get("id"):
            recs.append(rec)
    return sorted(recs, key=lambda r: (r.get("updated_ts", 0), r.get("updated_at", "")), reverse=True)


def record_path(sd, rid):
    if not ID_RE.fullmatch(rid or ""):
        raise Stop("bad_input", f"'{rid}' is not an interview id", "Run list and use one of its ids.")
    return sd / "interviews" / f"{rid}.json"


def load(sd, rid):
    if rid:
        f = record_path(sd, rid)
        if not f.exists():
            raise Stop("no_interview", f"no saved interview '{rid}'", "Run list to see saved interviews.")
        return json.loads(f.read_text(encoding="utf-8"))
    recs = all_records(sd)
    if not recs:
        raise Stop("no_interview", "no saved interview yet", "Run fetch first with the job link or company and role.")
    return recs[0]


def save(sd, rec):
    rec["updated_at"] = now()
    rec["updated_ts"] = time.time()
    f = record_path(sd, rec["id"])
    tmp = f.with_name(f".{f.stem}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, f)  # never leave a half-written record behind


@contextmanager
def locked(sd):
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


def find_existing(sd, posting, link=None):
    for r in all_records(sd):
        p = r["posting"]
        if (link and r.get("link") == link) or (posting and p.get("source_url") and p["source_url"] == posting.get("source_url")):
            return r
    return None


def new_id(sd, posting):
    base = f"{coach.slug(posting['company'], 20)}-{coach.slug(posting['title'], 30)}"
    rid, n = base, 2
    while (sd / "interviews" / f"{rid}.json").exists():
        rid, n = f"{base}-{n}", n + 1
    return rid


def build_record(sd, posting, link, when, existing=None):
    rec = existing or {"id": new_id(sd, posting), "created_at": now(), "asked": [], "questions": {}, "scores": []}
    rec.update({"link": link or rec.get("link"), "posting": posting, "skills": coach.extract(posting["lines"]),
                "facts": coach.facts(posting["lines"]), "keywords": coach.posting_keywords(posting["lines"], posting["company"])})
    rec.pop("closed_at", None)
    if when:
        rec["when"] = when
    return rec


def summary(rec, status):
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


# ---------------------------------------------------------------- commands

NEXT = {
    "bad_input": "Ask the student for the job link, or the company and exact role title.",
    "not_found": "Search the web for the posting and run fetch --url with it, or ask the student for the link.",
    "ambiguous": "Send the student up to three candidate links (each on its own line) and ask which; then run fetch --url.",
    "unreachable": "Read the job page with the web ability, save the text to a file, and run fetch --text-file.",
    "closed": "The posting is gone. Ask the student to paste the description or send a screenshot, then use --text-file.",
    "board_only": "Ask which role they're interviewing for, then run fetch --url <same link> --role \"<role>\".",
    "no_intern": "Tell the student there's no open intern posting like that right now (postings often close once "
                 "interviews start). Ask for the link from their application or a screenshot of the posting, or "
                 "offer the closest posting below.",
}


def _ambiguous(company, role, cands, location=None):
    us = [c for c in cands if boards.is_us(c.get("location"))]
    shown = boards.order_for_student(us or cands, location)[:6]
    return Stop("ambiguous", f"several {company} postings could match '{role}'", NEXT["ambiguous"],
                candidates=[{"n": i + 1, "title": c["title"], "location": c.get("location"), "url": c["url"],
                             "match": c["score"]} for i, c in enumerate(shown)])


def _from_candidates(sd, a, cands, company):
    pick = boards.choose(cands, location=a.location)
    if not pick:
        raise _ambiguous(company, a.role, cands, a.location)
    try:
        posting = boards.fetch_candidate(pick, a.company)
    except boards.BoardError as e:
        raise Stop(e.kind, e.message, NEXT.get(e.kind, ""))
    rec = build_record(sd, posting, pick["url"], a.when, find_existing(sd, posting))
    save(sd, rec)
    out = summary(rec, "matched")
    out["matched_title"], out["match"] = pick["title"], pick["score"]
    others = sorted({str(c.get("location")) for c in cands if boards.same_role(c["title"], pick["title"])
                     and c is not pick and c.get("location") != pick.get("location")})
    if others:
        out["other_offices"] = others[:8]
    if a.location and not boards.location_matches(a.location, pick.get("location")):
        out["location_note"] = f"No {a.location} posting for this role; this one is in {pick.get('location') or 'an unlisted place'}."
    elif not a.location and pick.get("location") and not boards.is_us(pick.get("location")):
        out["location_note"] = (f"This posting is in {pick['location']}. If the interview is for another office, "
                                "ask for that posting's link.")
    out["next"] = ("Tell the student which posting you used (office, and the link on its own line) so they can correct "
                   "it. " + out["next"])
    return out


def cmd_fetch(a, sd):
    if a.sample:
        d = json.loads((ASSETS / "sample_posting.json").read_text(encoding="utf-8"))
        posting = boards.posting_from_greenhouse(d, "saguaropay")
        rec = build_record(sd, posting, posting["source_url"], a.when or "Thu Oct 29, 2:00 PM (sample)",
                           find_existing(sd, posting))
        save(sd, rec)
        return summary(rec, "fetched")

    if a.text_file:
        if not (a.company and a.role):
            raise Stop("bad_input", "--text-file needs --company and --role", "Ask the student for the company and role.")
        lines = boards.text_to_lines(read_file(a.text_file, "posting text"))
        if len(lines) < 3:
            raise Stop("bad_input", "the posting text is nearly empty", "Read the job page again or ask for a screenshot.")
        posting = {"board": "pasted", "company": a.company, "title": a.role, "location": a.location or "",
                   "source_url": a.source_url or "", "fetched_at": now(), "lines": lines}
        rec = build_record(sd, posting, a.source_url, a.when, find_existing(sd, posting, a.source_url))
        save(sd, rec)
        return summary(rec, "from_text")

    if a.url:
        try:
            link = boards.parse_link(a.url)
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
        existing = find_existing(sd, None, a.url)
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
        rec = build_record(sd, posting, a.url, a.when, existing or find_existing(sd, posting))
        save(sd, rec)
        return summary(rec, "fetched")

    if a.company and a.role:
        try:
            cands = boards.search(a.company, a.role, a.location)
        except boards.BoardError as e:
            extra = {"closest": [{"title": c["title"], "location": c.get("location"), "url": c["url"]}
                                 for c in e.candidates]} if e.candidates else {}
            raise Stop(e.kind, e.message, NEXT.get(e.kind, ""), **extra)
        return _from_candidates(sd, a, cands, a.company)

    raise Stop("bad_input", "give --url, or --company and --role, or --text-file", "Ask the student for the job link.")


def cmd_brief(a, sd):
    rec = load(sd, a.id)
    if a.when:
        rec["when"] = a.when
    out_path = sd / "briefs" / f"{rec['id']}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(coach.brief_markdown(rec), encoding="utf-8")
    save(sd, rec)
    return {"ok": True, "id": rec["id"], "brief_path": str(out_path), "headline": coach.headline(rec),
            "texts": coach.brief_texts(rec),
            "next": "Turn brief_path into a one-page PDF or Google Doc and send it after these texts."}


def _best_scores(rec):
    best = {}
    for s in rec.get("scores", []):
        if s["question_id"] not in best or s["covered"] / s["total"] > best[s["question_id"]]["covered"] / best[s["question_id"]]["total"]:
            best[s["question_id"]] = s
    return best


def cmd_question(a, sd):
    rec = load(sd, a.id)
    tone = a.tone or rec.get("tone") or "chill"
    retry_of = None
    if a.retry:
        best = _best_scores(rec)
        if a.question_id:
            qid = a.question_id
        else:
            weak = sorted((s for s in best.values() if s["covered"] < s["total"]), key=lambda s: s["covered"] / s["total"])
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
    rec.setdefault("questions", {})[q["id"]] = {k: q[k] for k in ("id", "question", "term", "line", "quote", "points")}
    rec["pending_question"] = q["id"]
    rec.pop("follow_up", None)  # a new question (or a redo) replaces any open follow-up
    rec["tone"] = tone
    save(sd, rec)
    out = {"ok": True, "id": rec["id"], "question_id": q["id"], "question": q["question"], "term": q["term"],
           "why": q["why"], "tone": tone, "call_script": q["call_script"], "text_version": q["text_version"],
           "listening_for": [p["label"] for p in q["points"]],
           "score_command": f"python3 scripts/callback.py score --id {rec['id']} --question-id {q['id']} --answer-file <file>",
           "next": "Ask it now by text, or put call_script and text_version word for word into a scheduled call. "
                   "When the answer arrives, run score_command."}
    if retry_of:
        out["retry_of"] = f"{retry_of['covered']}/{retry_of['total']}"
    return out


def cmd_score(a, sd):
    waiting = [] if a.id else [r for r in all_records(sd) if r.get("pending_question")]
    if len(waiting) > 1 and not a.credit:
        raise Stop("ambiguous", "several interviews are waiting for an answer",
                   "Ask the student which one this answer is for, then pass --id.",
                   candidates=[{"id": r["id"], "company": r["posting"]["company"], "title": r["posting"]["title"],
                                "question": (r.get("questions") or {}).get(r["pending_question"], {}).get("question")}
                               for r in waiting[:4]])
    rec = waiting[0] if waiting else load(sd, a.id)
    qid = a.question_id or rec.get("pending_question") or rec.get("last_question")
    q = (rec.get("questions") or {}).get(qid or "")
    if not q:
        raise Stop("bad_input", "no question is waiting for an answer",
                   "Run question first, or pass --question-id (list shows the interview's questions).")
    if a.credit:
        return _credit(sd, rec, qid, a.credit)
    follow = rec.get("follow_up") if not a.question_id or a.question_id == (rec.get("follow_up") or {}).get("question_id") else None
    if follow and follow.get("question_id") == qid:
        q = dict(q, points=[p for p in q["points"] if p["label"] in follow["points"]])
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
    before = None if follow else _best_scores(rec).get(qid)
    result = coach.score_answer(q, answer)
    if follow:
        result["tips"] = []
        result["follow_up_of"] = qid
    rec.setdefault("scores", []).append({"at": now(), "question_id": qid, "covered": result["covered"],
                                         "total": result["total"], "verdict": result["verdict"], "missed": result["missed"]})
    rec.pop("pending_question", None)
    rec.pop("follow_up", None)
    rec["last_question"] = qid
    head = f"{result['covered']}/{result['total']} key points: {result['verdict']}."
    if before:
        head = f"{before['covered']}/{before['total']} last time, {result['covered']}/{result['total']} now: {result['verdict']}."
        result["progress"] = f"{before['covered']}/{before['total']} -> {result['covered']}/{result['total']}"
    texts = [head]
    hit = [p["point"] for p in result["points"] if p["covered"]]
    if hit:
        texts.append("Covered: " + "; ".join(hit) + ".")
    if result["missed"]:
        cited = next((p["cite"] for p in result["points"] if not p["covered"] and p.get("cite")), None)
        if cited:
            cite = f" The posting says \"{cited['quote']}\" (line {cited['line']})."
        elif q.get("term") and q.get("line"):
            cite = f" The posting asks for {q['term']} on line {q['line']}."
        else:
            cite = ""
        texts.append("Missed: " + "; ".join(result["missed"]) + "." + cite)
        if rec.get("tone") == "tough" and not follow:
            result["follow_up"] = f"Follow-up: you didn't cover {result['missed'][0]}. How would you handle that here?"
            texts.append(result["follow_up"])
            rec["follow_up"] = {"question_id": qid, "points": result["missed"]}
            rec["pending_question"] = qid
    save(sd, rec)
    texts += result["tips"][:2]
    result.update({"ok": True, "id": rec["id"], "texts": texts,
                   "next": "Send the texts one by one. Then offer a redo of this question (question --retry) or the "
                           "next one (question), each as a scheduled check-in at a time the student picks."})
    return result


def _credit(sd, rec, qid, label):
    """Cubbie judged that the answer made a point in other words: record it, so redos and weak spots agree."""
    last = next((s for s in reversed(rec.get("scores", [])) if s["question_id"] == qid), None)
    match = next((m for m in (last or {}).get("missed", []) if label.lower() in m.lower()), None)
    if not match:
        raise Stop("bad_input", f"'{label}' isn't a missed point of the last answer to {qid}",
                   "Pass part of a missed point's label exactly as score printed it.")
    last["missed"].remove(match)
    last["covered"] += 1
    last["credited"] = last.get("credited", []) + [match]
    last["verdict"] = "strong" if last["covered"] / last["total"] >= 0.75 else "solid" if last["covered"] / last["total"] >= 0.5 else "needs work"
    if rec.get("follow_up", {}).get("question_id") == qid:
        rec["follow_up"]["points"] = [p for p in rec["follow_up"]["points"] if p != match]
        if not rec["follow_up"]["points"]:
            rec.pop("follow_up")
            rec.pop("pending_question", None)
    save(sd, rec)
    return {"ok": True, "id": rec["id"], "question_id": qid, "credited": match,
            "score": f"{last['covered']}/{last['total']}", "next": "Tell the student in one line that the point counts."}


def cmd_invite(a, sd):
    if a.email_file:
        text = read_file(a.email_file, "email")
    elif a.sample:
        text = (ASSETS / "sample_invite.txt").read_text(encoding="utf-8")
    else:
        raise Stop("bad_input", "give --email-file", "Save the email text to a file and pass it.")
    out = coach.classify_email(text, boards.parse_link)
    out["ok"] = True
    return out


def cmd_list(a, sd):
    items = []
    for r in all_records(sd):
        last = (r.get("scores") or [None])[-1]
        items.append({"id": r["id"], "company": r["posting"]["company"], "title": r["posting"]["title"],
                      "when": r.get("when"), "questions_asked": len(r.get("asked", [])),
                      "waiting_for_answer": r.get("pending_question"),
                      "last_score": f"{last['covered']}/{last['total']}" if last else None,
                      "weak_spots": sorted({m for s in {x["question_id"]: x for x in r.get("scores", [])}.values()
                                            for m in s["missed"]})[:5]})
    return {"ok": True, "interviews": items}


def cmd_forget(a, sd):
    if a.all:
        files = list((sd / "interviews").glob("*.json")) + list((sd / "briefs").glob("*.md"))
        for f in files:
            f.unlink()
        n = sum(1 for f in files if f.suffix == ".json")
        if not a.sample:
            for f in list((sd / "sample" / "interviews").glob("*.json")) + list((sd / "sample" / "briefs").glob("*.md")):
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


def main(argv=None):
    boards.DEADLINE = time.monotonic() + 100  # stay well inside the sandbox's 2-minute limit
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
    watchdog = threading.Timer(110, _out_of_time)
    watchdog.daemon = True
    watchdog.start()
    try:
        a = ap.parse_args(argv)
    except Stop as e:
        emit({"ok": False, "error": e.kind, "message": e.message, "next": e.next}, EXIT[e.kind])
    handlers = {"fetch": cmd_fetch, "brief": cmd_brief, "question": cmd_question, "score": cmd_score,
                "invite": cmd_invite, "list": cmd_list, "forget": cmd_forget}
    try:
        sd = state_dir(a.sample)
        with locked(sd):
            result = handlers[a.cmd](a, sd)
        emit(result)
    except Stop as e:
        emit({"ok": False, "error": e.kind, "message": e.message, "next": e.next, **e.extra}, EXIT.get(e.kind, 1))
    except boards.BoardError as e:
        emit({"ok": False, "error": e.kind, "message": e.message, "next": NEXT.get(e.kind, "")}, EXIT.get(e.kind, 1))
    except Exception as e:  # never hand Cubbie a traceback
        emit({"ok": False, "error": "error", "message": f"{type(e).__name__}: {e}",
              "where": traceback.format_exc(limit=3).splitlines()[-3:],
              "next": "Tell the student the prep tool hit an error and keep helping from the posting text."}, 1)


if __name__ == "__main__":
    main()
