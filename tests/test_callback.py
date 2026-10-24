"""Offline tests for the interview-callback Skill. Run from the repo root: python -m unittest discover -s tests -v"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "interview-callback"
sys.path.insert(0, str(SKILL / "scripts"))

import boards  # noqa: E402
import callback  # noqa: E402
import coach  # noqa: E402

SAMPLE = json.loads((SKILL / "assets" / "sample_posting.json").read_text(encoding="utf-8"))

LEVER_JOB = {
    "id": "aaaa-1111", "text": "Data Engineer Intern", "hostedUrl": "https://jobs.lever.co/mesa/aaaa-1111",
    "categories": {"location": "Phoenix, AZ"},
    "description": "<p>Mesa Analytics helps hospitals plan staffing.</p>",
    "lists": [{"text": "Requirements", "content": "<li>Python and SQL</li><li>Experience with Airflow or dbt</li>"},
              {"text": "Nice to have", "content": "<li>Spark</li>"}],
    "additional": "<p>Pay: $30/hr</p>",
}
ASHBY_BOARD = {"jobs": [{"id": "bbbb-2222", "title": "Product Manager Intern", "location": "Remote (US)",
                         "jobUrl": "https://jobs.ashbyhq.com/cactus/bbbb-2222",
                         "descriptionHtml": "<h3>What you'll do</h3><ul><li>Run user research and write PRDs.</li></ul>"
                                            "<h3>Qualifications</h3><ul><li>Strong communication and A/B testing basics.</li></ul>"}]}
SMART_JOB = {"name": "Finance Intern", "company": {"name": "Ocotillo Bank"}, "location": {"city": "Tempe", "region": "AZ"},
             "jobAd": {"sections": {"jobDescription": {"title": "Job Description", "text": "<p>Build financial models in Excel.</p>"},
                                    "qualifications": {"title": "Qualifications", "text": "<ul><li>Accounting coursework</li></ul>"}}}}
WORKDAY_JOB = {"jobPostingInfo": {"title": "Hardware Validation Intern", "location": "US, Arizona, Chandler",
                                  "jobDescription": "<p><b>Qualifications:</b></p><ul><li>Python and Verilog</li><li>Lab debug with an oscilloscope</li></ul>",
                                  "externalUrl": "https://chipco.wd1.myworkdayjobs.com/External/job/Chandler/Hardware-Validation-Intern_JR1"},
               "hiringOrganization": {"name": "ChipCo Legal Entity LLC"}}
WEBPAGE = """<html><head><title>Careers</title><script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [{"@type": "JobPosting", "title": "iOS Engineer Intern",
 "hiringOrganization": {"name": "Palo Verde"}, "jobLocation": {"address": {"addressLocality": "Tempe", "addressRegion": "AZ"}},
 "description": "<p>Requirements:</p><ul><li>Swift and SwiftUI</li><li>Git</li></ul>"}]}
</script></head><body>...</body></html>"""
GREENHOUSE_LIST = {"jobs": [
    {"id": 1, "title": "Software Engineer, Intern", "absolute_url": "https://x/1", "location": {"name": "London"}},
    {"id": 2, "title": "Software Engineer, Intern", "absolute_url": "https://x/2", "location": {"name": "Seattle, WA"}},
    {"id": 3, "title": "Account Executive", "absolute_url": "https://x/3", "location": {"name": "Seattle, WA"}},
]}


class FakeWeb:
    """Answers boards.http_get from a dict of url -> (status, body)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, data=None, headers=None):
        self.calls.append(url)
        for prefix, (status, body) in self.routes.items():
            if url.startswith(prefix):
                return status, body if isinstance(body, str) else json.dumps(body)
        return 404, ""


def run(*argv):
    """Run the CLI; return (exit code, parsed JSON)."""
    out = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out):
        try:
            callback.main(list(argv))
        except SystemExit as e:
            code = e.code or 0
    return code, json.loads(out.getvalue())


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["INTERVIEW_CALLBACK_HOME"] = self.tmp.name
        self._http = boards.http_get

    def tearDown(self):
        boards.http_get = self._http
        os.environ.pop("INTERVIEW_CALLBACK_HOME", None)
        self.tmp.cleanup()

    def web(self, routes):
        boards.http_get = FakeWeb(routes)
        return boards.http_get


class TestLinks(unittest.TestCase):
    def test_parse_each_board(self):
        cases = {
            "https://boards.greenhouse.io/stripe/jobs/123": ("greenhouse", "stripe", "123"),
            "https://job-boards.greenhouse.io/ramp/jobs/9": ("greenhouse", "ramp", "9"),
            "https://stripe.com/jobs/search?gh_jid=8130867": ("greenhouse", "stripe", "8130867"),
            "https://boards.greenhouse.io/embed/job_app?for=acme&token=55": ("greenhouse", "acme", "55"),
            "https://jobs.lever.co/palantir/6ed7-uuid": ("lever", "palantir", "6ed7-uuid"),
            "https://jobs.ashbyhq.com/ramp/3441-uuid": ("ashby", "ramp", "3441-uuid"),
            "https://jobs.smartrecruiters.com/Visa/743999-software-engineer": ("smartrecruiters", "Visa", "743999"),
        }
        for url, (board, slug, job_id) in cases.items():
            link = boards.parse_link(url)
            self.assertEqual((link["board"], link["slug"], link["job_id"]), (board, slug, job_id), url)

    def test_workday_link_drops_locale(self):
        link = boards.parse_link("https://intel.wd1.myworkdayjobs.com/en-US/External/job/US-Arizona-Chandler/Intern_JR02")
        self.assertEqual((link["board"], link["slug"], link["site"], link["job_path"]),
                         ("workday", "intel", "External", "job/US-Arizona-Chandler/Intern_JR02"))

    def test_unknown_site_is_webpage_and_junk_is_rejected(self):
        self.assertEqual(boards.parse_link("https://careers.example.com/jobs/42")["board"], "webpage")
        with self.assertRaises(boards.BoardError):
            boards.parse_link("not a link")


class TestText(unittest.TestCase):
    def test_html_to_lines_handles_escaped_html_lists_and_quotes(self):
        lines = boards.html_to_lines("&lt;h2&gt;What you’ll do&lt;/h2&gt;&lt;ul&gt;&lt;li&gt;Build things&lt;/li&gt;&lt;/ul&gt;")
        self.assertEqual(lines, ["What you'll do", "- Build things"])

    def test_long_paragraphs_split_by_sentence(self):
        text = " ".join(["This sentence is about forty characters."] * 12)
        self.assertTrue(all(len(x) <= 280 for x in boards.html_to_lines(f"<p>{text}</p>")))


class TestExtraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lines = boards.posting_from_greenhouse(SAMPLE, "saguaropay")["lines"]
        cls.skills = {s["term"]: s for s in coach.extract(cls.lines)}

    def test_sections(self):
        secs = coach.sections(self.lines)
        self.assertEqual(secs[0], "heading")
        self.assertEqual(secs[1], "about")
        self.assertEqual(secs[self.lines.index("- Experience with Docker or Kubernetes.")], "nice")

    def test_kafka_is_the_top_requirement_with_its_line(self):
        top = coach.extract(self.lines)[0]
        self.assertEqual((top["term"], top["kind"], top["line"], top["mentions"]), ("Kafka", "must", 14, 3))

    def test_nice_to_have_and_about_text(self):
        self.assertEqual(self.skills["Docker"]["kind"], "nice")
        self.assertNotIn("Accounting", self.skills)  # "reconcile every dollar" is in the about-us text

    def test_word_boundaries(self):
        found = {s["term"] for s in coach.extract(["- Go above and beyond for merchants.", "- Spring 2027 internship",
                                                   "- Rest of the team is great", "- Experience with Java, Go, or R."])}
        self.assertIn("Go", found)
        self.assertIn("R", found)
        self.assertNotIn("Spring", found)
        self.assertNotIn("APIs", found)

    def test_facts_and_missing_facts(self):
        fx = coach.facts(self.lines)
        self.assertEqual(fx["pay"]["line"], 20)
        self.assertEqual(fx["graduation"]["line"], 11)
        self.assertNotIn("interview process", fx)


class TestFlow(Base):
    def test_sample_flow_end_to_end(self):
        code, out = run("fetch", "--sample")
        self.assertEqual((code, out["top_skills"][0]["term"]), (0, "Kafka"))
        code, out = run("brief", "--sample")
        brief = Path(out["brief_path"]).read_text(encoding="utf-8")
        self.assertIn("(line 14)", brief)
        self.assertIn("## Not in the posting: ask your recruiter", brief)
        self.assertIn("14. - Curiosity about distributed systems", brief)
        code, out = run("question", "--sample", "--tone", "tough")
        self.assertEqual(out["question_id"], "kafka-1")
        self.assertIn("tough interviewer", out["call_script"])
        self.assertIn("voice note", out["call_script"])
        code, out = run("score", "--sample")
        self.assertEqual((out["covered"], out["total"]), (3, 4))
        self.assertEqual(out["missed"], ["idempotency (a unique key per payment)"])
        self.assertIn("(line 7)", out["texts"][2])
        code, out = run("list", "--sample")
        self.assertEqual(out["interviews"][0]["weak_spots"], ["idempotency (a unique key per payment)"])

    def test_sample_state_is_separate(self):
        run("fetch", "--sample")
        code, out = run("list")
        self.assertEqual(out["interviews"], [])

    def test_question_order_and_exhaustion(self):
        run("fetch", "--sample")
        ids = []
        while True:
            code, out = run("question", "--sample")
            if code:
                break
            ids.append(out["question_id"])
        self.assertEqual(code, 8)
        self.assertEqual(ids[:2], ["kafka-1", "why-company"])
        self.assertEqual(len(ids), len(set(ids)))

    def test_forget_needs_an_id_and_unknown_term_is_explained(self):
        run("fetch", "--sample")
        self.assertEqual(run("forget", "--sample")[0], 2)
        code, out = run("question", "--sample", "--term", "Haskell")
        self.assertEqual(code, 2)
        self.assertIn("Kafka", out["next"])
        code, out = run("list", "--sample")
        self.assertEqual(run("forget", "--sample", "--id", out["interviews"][0]["id"])[0], 0)
        self.assertEqual(run("list", "--sample")[1]["interviews"], [])

    def test_score_needs_a_real_answer(self):
        run("fetch", "--sample")
        run("question", "--sample")
        code, out = run("score", "--sample", "--answer", "idk")
        self.assertEqual(code, 2)

    def test_each_board_fetches(self):
        self.web({
            "https://api.lever.co/v0/postings/mesa/aaaa-1111": (200, LEVER_JOB),
            "https://api.ashbyhq.com/posting-api/job-board/cactus": (200, ASHBY_BOARD),
            "https://api.smartrecruiters.com/v1/companies/ocotillo/postings/777": (200, SMART_JOB),
            "https://chipco.wd1.myworkdayjobs.com/wday/cxs/chipco/External/job/": (200, WORKDAY_JOB),
            "https://careers.paloverde.example/jobs/ios": (200, WEBPAGE),
        })
        expect = {
            "https://jobs.lever.co/mesa/aaaa-1111": ("lever", "Data Engineer Intern", "Python"),
            "https://jobs.ashbyhq.com/cactus/bbbb-2222": ("ashby", "Product Manager Intern", "Product sense"),
            "https://jobs.smartrecruiters.com/ocotillo/777-finance-intern": ("smartrecruiters", "Finance Intern", "Financial modeling"),
            "https://chipco.wd1.myworkdayjobs.com/en-US/External/job/Chandler/Hardware-Validation-Intern_JR1":
                ("workday", "Hardware Validation Intern", "Verilog/VHDL"),
            "https://careers.paloverde.example/jobs/ios": ("webpage", "iOS Engineer Intern", "Swift"),
        }
        for url, (board, title, skill) in expect.items():
            code, out = run("fetch", "--url", url)
            self.assertEqual(code, 0, out)
            self.assertEqual((out["board"], out["title"]), (board, title))
            self.assertIn(skill, [s["term"] for s in out["top_skills"]], url)
        self.assertEqual(run("list")[1]["interviews"][1]["company"], "Chipco")  # tenant name, not the legal entity

    def test_closed_posting_uses_saved_copy(self):
        url = "https://jobs.lever.co/mesa/aaaa-1111"
        self.web({"https://api.lever.co/v0/postings/mesa/aaaa-1111": (200, LEVER_JOB)})
        self.assertEqual(run("fetch", "--url", url)[0], 0)
        self.web({})  # now everything is 404
        code, out = run("fetch", "--url", url)
        self.assertEqual((code, out["status"]), (0, "closed_using_saved_copy"))
        code, out = run("brief")
        self.assertIn("the posting was closed", Path(out["brief_path"]).read_text(encoding="utf-8"))

    def test_closed_without_copy_and_unreachable(self):
        self.web({})
        code, out = run("fetch", "--url", "https://jobs.lever.co/mesa/gone")
        self.assertEqual((code, out["error"]), (6, "closed"))

        def down(url, data=None, headers=None):
            raise boards.BoardError("unreachable", "boom")
        boards.http_get = down
        code, out = run("fetch", "--url", "https://jobs.lever.co/mesa/x")
        self.assertEqual(code, 5)
        self.assertIn("--text-file", out["next"])

    def test_text_file_fallback(self):
        f = Path(self.tmp.name) / "posting.txt"
        f.write_text("Requirements:\n- Python and SQL\n- Tableau dashboards\nPay: $25/hr\n", encoding="utf-8")
        code, out = run("fetch", "--text-file", str(f), "--company", "Desert Co", "--role", "Data Analyst Intern")
        self.assertEqual((code, out["board"]), (0, "pasted"))
        self.assertEqual([s["term"] for s in out["top_skills"]][:2], ["Python", "SQL"])
        code, out = run("fetch", "--text-file", str(f))
        self.assertEqual(code, 2)

    def test_search_prefers_us_office_and_reports_others(self):
        self.web({"https://boards-api.greenhouse.io/v1/boards/acme/jobs/2": (200, dict(SAMPLE, title="Software Engineer, Intern")),
                  "https://boards-api.greenhouse.io/v1/boards/acme/jobs": (200, GREENHOUSE_LIST),
                  "https://api.lever.co/v0/postings/acme": (404, ""), "https://api.ashbyhq.com": (404, "")})
        code, out = run("fetch", "--company", "Acme", "--role", "SWE intern")
        self.assertEqual((code, out["status"]), (0, "matched"))
        self.assertEqual(out["other_offices"], ["London"])
        self.assertTrue(boards.http_get.calls[-1].endswith("/jobs/2"))

    def test_search_ambiguous_and_not_found(self):
        listing = {"jobs": [{"id": 1, "title": "Software Engineer Intern, iOS", "absolute_url": "https://x/1"},
                            {"id": 2, "title": "Software Engineer Intern, Backend", "absolute_url": "https://x/2"}]}
        self.web({"https://boards-api.greenhouse.io/v1/boards/acme/jobs": (200, listing)})
        code, out = run("fetch", "--company", "Acme", "--role", "Software Engineer Intern")
        self.assertEqual(code, 4)
        self.assertEqual(len(out["candidates"]), 2)
        self.web({})
        code, out = run("fetch", "--company", "Nowhere Inc", "--role", "Intern")
        self.assertEqual(code, 3)


class TestInvites(unittest.TestCase):
    def classify(self, text):
        return coach.classify_email(text, boards.parse_link)

    def test_sample_invite(self):
        out = self.classify((SKILL / "assets" / "sample_invite.txt").read_text(encoding="utf-8"))
        self.assertEqual(out["category"], "interview_confirmed")
        self.assertEqual(out["job_links"][0]["board"], "greenhouse")
        self.assertEqual(out["company_hint"], "Saguaropay")

    def test_other_categories(self):
        self.assertEqual(self.classify("Unfortunately we will not be moving forward with your interview.")["category"], "rejection")
        self.assertEqual(self.classify("Please complete the HackerRank online assessment within 7 days.")["category"], "assessment")
        self.assertEqual(self.classify("We'd like to schedule an interview. Pick a time: https://calendly.com/acme/30")["category"],
                         "schedule_request")
        self.assertEqual(self.classify("From: deals@shop.com\nBig sale this weekend")["category"], "other")


class TestScoring(unittest.TestCase):
    def test_short_words_need_whole_words_and_numbers_count(self):
        q = {"id": "x", "line": None, "points": [
            {"label": "uses a set", "phrases": ["set"], "number": False},
            {"label": "a result", "phrases": [], "number": True}]}
        self.assertFalse(coach.score_answer(q, "I changed the settings")["points"][0]["covered"])
        r = coach.score_answer(q, "I used a set and it cut latency by 40 percent")
        self.assertEqual(r["covered"], 2)

    def test_we_versus_i_tip(self):
        q = {"id": "x", "line": None, "points": [{"label": "p", "phrases": ["kafka"], "number": False}]}
        answer = "We built it and we tested it and we shipped it and our team liked it " * 3
        self.assertTrue(any("\"we\"" in t for t in coach.score_answer(q, answer)["tips"]))


if __name__ == "__main__":
    unittest.main()
