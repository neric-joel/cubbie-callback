"""interview-callback: prep a student for one interview from the real job posting.

Run from the Skill folder:
  python3 scripts/callback.py fetch --url <job link>                      # or --company X --role Y, or --text-file
  python3 scripts/callback.py brief [--id ID] [--when "Thu Oct 29, 2 PM Arizona"]
  python3 scripts/callback.py question [--id ID] [--tone chill|tough] [--term Kafka]
  python3 scripts/callback.py score [--id ID] --answer-file answer.txt
  python3 scripts/callback.py invite --email-file invite.txt
  python3 scripts/callback.py list | forget --id ID
Add --sample to any command to use the bundled example (no network, separate saved state).

Output is JSON. Exit codes: 0 ok, 2 missing or bad input, 3 no posting found, 4 several possible postings,
5 job board unreachable, 6 posting closed and no saved copy, 7 no saved interview, 8 every question asked.
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import boards  # noqa: E402
import coach  # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "assets"
EXIT = {"bad_input": 2, "not_found": 3, "ambiguous": 4, "unreachable": 5, "closed": 6, "no_interview": 7, "done": 8}


class Stop(Exception):
    def __init__(self, kind, message, nxt, **extra):
        super().__init__(message)
        self.kind, self.message, self.next, self.extra = kind, message, nxt, extra


def emit(data, code=0):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=1)[:7900])
    sys.exit(code)


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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
            recs.append(json.loads(f.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return sorted(recs, key=lambda r: r.get("updated_at", ""), reverse=True)


def load(sd, rid):
    if rid:
        f = sd / "interviews" / f"{rid}.json"
        if not f.exists():
            raise Stop("no_interview", f"no saved interview '{rid}'", "Run list to see saved interviews.")
        return json.loads(f.read_text(encoding="utf-8"))
    recs = all_records(sd)
    if not recs:
        raise Stop("no_interview", "no saved interview yet", "Run fetch first with the job link or company and role.")
    return recs[0]


def save(sd, rec):
    rec["updated_at"] = now()
    (sd / "interviews" / f"{rec['id']}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")


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
    return {"ok": True, "status": status, "id": rec["id"], "company": p["company"], "title": p["title"],
            "location": p["location"], "board": p["board"], "source_url": p["source_url"], "lines": len(p["lines"]),
            "headline": coach.headline(rec), "top_skills": top,
            "next": "Run brief, then offer prep time and a mock interview."}


# ---------------------------------------------------------------- commands

def cmd_fetch(a, sd):
    if a.sample:
        d = json.loads((ASSETS / "sample_posting.json").read_text(encoding="utf-8"))
        posting = boards.posting_from_greenhouse(d, "saguaropay")
        posting["fetched_at"] = now()
        rec = build_record(sd, posting, posting["source_url"], a.when or "Thu 2:00 PM (sample)",
                           find_existing(sd, posting))
        save(sd, rec)
        return summary(rec, "fetched")

    if a.text_file:
        if not (a.company and a.role):
            raise Stop("bad_input", "--text-file needs --company and --role", "Ask the student for the company and role.")
        text = Path(a.text_file).read_text(encoding="utf-8", errors="replace")
        lines = boards.text_to_lines(text)
        if len(lines) < 3:
            raise Stop("bad_input", "the posting text is nearly empty", "Read the job page again or ask for a screenshot.")
        posting = {"board": "pasted", "company": a.company, "title": a.role, "location": a.location or "",
                   "source_url": a.source_url or "", "fetched_at": now(), "lines": lines}
        rec = build_record(sd, posting, a.source_url, a.when, find_existing(sd, posting, a.source_url))
        save(sd, rec)
        return summary(rec, "from_text")

    if a.url:
        existing = find_existing(sd, None, a.url)
        try:
            posting = boards.fetch_by_link(a.url, a.company)
        except boards.BoardError as e:
            if e.kind == "closed" and existing:
                existing["closed_at"] = existing.get("closed_at") or now()
                save(sd, existing)
                out = summary(existing, "closed_using_saved_copy")
                out["note"] = f"The posting is closed now; using the copy saved {existing['posting']['fetched_at'][:10]}."
                return out
            raise Stop(e.kind, e.message, NEXT[e.kind])
        rec = build_record(sd, posting, a.url, a.when, existing or find_existing(sd, posting))
        save(sd, rec)
        return summary(rec, "fetched")

    if a.company and a.role:
        try:
            cands = boards.search(a.company, a.role, a.location)
        except boards.BoardError as e:
            raise Stop(e.kind, e.message, NEXT[e.kind])
        pick = boards.choose(cands, location=a.location)
        if not pick:
            raise Stop("ambiguous", f"several {a.company} postings could match '{a.role}'", NEXT["ambiguous"],
                       candidates=[{"n": i + 1, "title": c["title"], "location": c.get("location"), "url": c["url"],
                                    "match": c["score"]} for i, c in enumerate(cands[:6])])
        try:
            posting = boards.fetch_candidate(pick, a.company)
        except boards.BoardError as e:
            raise Stop(e.kind, e.message, NEXT[e.kind])
        rec = build_record(sd, posting, pick["url"], a.when, find_existing(sd, posting))
        save(sd, rec)
        out = summary(rec, "matched")
        out["matched_title"], out["match"] = pick["title"], pick["score"]
        others = sorted({str(c.get("location")) for c in cands if boards.same_role(c["title"], pick["title"])
                         and c is not pick and c.get("location") != pick.get("location")})
        if others:
            out["other_offices"] = others[:8]
        if a.location and a.location.lower() not in str(pick.get("location") or "").lower():
            out["location_note"] = f"No {a.location} posting for this role; this one is in {pick.get('location') or 'an unlisted location'}."
        out["next"] = ("Tell the student which posting you used (office and link on its own line) so they can correct "
                       "it, then run brief." + (" The same role is posted for other offices too." if others else ""))
        return out

    raise Stop("bad_input", "give --url, or --company and --role, or --text-file", "Ask the student for the job link.")


NEXT = {
    "bad_input": "Ask the student for the job link, or the company and exact role title.",
    "not_found": "Search the web for the posting and run fetch --url with it, or ask the student for the link.",
    "ambiguous": "Send the student the candidate links (each on its own line) and ask which one; then run fetch --url.",
    "unreachable": "Read the job page with the web ability, save the text to a file, and run fetch --text-file.",
    "closed": "The posting is gone. Ask the student to paste the job description or send a screenshot, then use --text-file.",
}


def cmd_brief(a, sd):
    rec = load(sd, a.id)
    if a.when:
        rec["when"] = a.when
    out_path = Path(a.out) if a.out else sd / "briefs" / f"{rec['id']}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(coach.brief_markdown(rec), encoding="utf-8")
    save(sd, rec)
    p = rec["posting"]
    return {"ok": True, "id": rec["id"], "brief_path": str(out_path), "headline": coach.headline(rec),
            "texts": [f"{p['company']} {p['title']}: your prep brief is ready.", coach.headline(rec),
                      "Every point cites a line of the posting, so you can check it."],
            "next": "Turn brief_path into a one-page PDF or Google Doc and send it with these texts."}


def cmd_question(a, sd):
    rec = load(sd, a.id)
    q = coach.next_question(rec, a.tone, a.question_id, a.term)
    if not q and (a.term or a.question_id):
        known = sorted({x["term"] for x in coach.plan_questions(rec) if x["term"]})
        raise Stop("bad_input", f"no unasked question for '{a.term or a.question_id}'",
                   "Pick one of these skills from the posting: " + ", ".join(known))
    if not q:
        raise Stop("done", "every planned question has been asked", "Offer a fresh round tomorrow (run question --term) or stop.")
    rec.setdefault("asked", [])
    if q["id"] not in rec["asked"]:
        rec["asked"].append(q["id"])
    rec.setdefault("questions", {})[q["id"]] = {k: q[k] for k in ("id", "question", "term", "line", "quote", "points")}
    rec["last_question"] = q["id"]
    rec["tone"] = a.tone
    save(sd, rec)
    return {"ok": True, "id": rec["id"], "question_id": q["id"], "question": q["question"], "term": q["term"],
            "why": q["why"], "tone": a.tone, "call_script": q["call_script"], "text_version": q["text_version"],
            "listening_for": [p["label"] for p in q["points"]],
            "next": "Ask it by phone call or text. When the answer arrives (voice note transcript or text), run score."}


def cmd_score(a, sd):
    rec = load(sd, a.id)
    qid = a.question_id or rec.get("last_question")
    q = (rec.get("questions") or {}).get(qid or "")
    if not q:
        raise Stop("bad_input", "no question has been asked yet", "Run question first.")
    if a.answer_file:
        answer = Path(a.answer_file).read_text(encoding="utf-8", errors="replace")
    elif a.answer:
        answer = a.answer
    elif a.sample:
        answer = (ASSETS / "sample_answer.txt").read_text(encoding="utf-8")
    else:
        raise Stop("bad_input", "give --answer-file or --answer", "Transcribe the voice note to a file and pass it.")
    if len(answer.split()) < 5:
        raise Stop("bad_input", "the answer is only a few words", "Ask the student to answer again in a voice note.")
    result = coach.score_answer(q, answer)
    rec.setdefault("scores", []).append({"at": now(), "question_id": qid, "covered": result["covered"],
                                         "total": result["total"], "verdict": result["verdict"], "missed": result["missed"]})
    save(sd, rec)
    texts = [f"{result['covered']}/{result['total']} key points: {result['verdict']}."]
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
    texts += result["tips"][:2]
    result.update({"ok": True, "id": rec["id"], "texts": texts,
                   "next": "Send the texts, then offer the next question (run question) or a check-in before the interview."})
    return result


def cmd_invite(a, sd):
    path = Path(a.email_file) if a.email_file else (ASSETS / "sample_invite.txt" if a.sample else None)
    if not path:
        raise Stop("bad_input", "give --email-file", "Save the email text to a file and pass it.")
    out = coach.classify_email(path.read_text(encoding="utf-8", errors="replace"), boards.parse_link)
    out["ok"] = True
    return out


def cmd_list(a, sd):
    items = []
    for r in all_records(sd):
        last = (r.get("scores") or [None])[-1]
        items.append({"id": r["id"], "company": r["posting"]["company"], "title": r["posting"]["title"],
                      "when": r.get("when"), "questions_asked": len(r.get("asked", [])),
                      "last_score": f"{last['covered']}/{last['total']}" if last else None,
                      "weak_spots": sorted({m for s in r.get("scores", []) for m in s["missed"]})[:5]})
    return {"ok": True, "interviews": items}


def cmd_forget(a, sd):
    if not a.id:
        raise Stop("bad_input", "forget needs --id", "Run list, confirm with the student which interview to delete, then pass its id.")
    rec = load(sd, a.id)
    (sd / "interviews" / f"{rec['id']}.json").unlink()
    brief = sd / "briefs" / f"{rec['id']}.md"
    if brief.exists():
        brief.unlink()
    return {"ok": True, "deleted": rec["id"]}


def cmd_reset_sample(a, sd):
    shutil.rmtree(state_dir(True), ignore_errors=True)
    return {"ok": True, "reset": "sample state"}


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--sample", action="store_true", help="use the bundled example; no network; separate state")
    common.add_argument("--id", help="saved interview id (default: the most recent)")
    ap = argparse.ArgumentParser(prog="callback.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
    b.add_argument("--out")
    b.add_argument("--when")
    q = sub.add_parser("question", parents=[common])
    q.add_argument("--tone", choices=["chill", "tough"], default="chill")
    q.add_argument("--term")
    q.add_argument("--question-id")
    s = sub.add_parser("score", parents=[common])
    s.add_argument("--answer-file")
    s.add_argument("--answer")
    s.add_argument("--question-id")
    i = sub.add_parser("invite", parents=[common])
    i.add_argument("--email-file")
    sub.add_parser("list", parents=[common])
    sub.add_parser("forget", parents=[common])
    sub.add_parser("reset-sample", parents=[common])
    a = ap.parse_args(argv)
    sd = state_dir(a.sample)
    handlers = {"fetch": cmd_fetch, "brief": cmd_brief, "question": cmd_question, "score": cmd_score,
                "invite": cmd_invite, "list": cmd_list, "forget": cmd_forget, "reset-sample": cmd_reset_sample}
    try:
        emit(handlers[a.cmd](a, sd))
    except Stop as e:
        emit({"ok": False, "error": e.kind, "message": e.message, "next": e.next, **e.extra}, EXIT[e.kind])
    except boards.BoardError as e:
        emit({"ok": False, "error": e.kind, "message": e.message, "next": NEXT.get(e.kind, "")}, EXIT.get(e.kind, 5))


if __name__ == "__main__":
    main()
