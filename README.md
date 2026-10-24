# Callback: interview prep from the real job posting, in iMessage

A [Cubbie](https://cubbie.ai) Skill. When an interview invite lands, Cubbie pulls the company's actual job posting,
texts you what it asks for most (every point cites a numbered line of the posting), books prep time, and runs a
mock interview by phone call or voice note. Then it scores your answer against what the posting asked for.

Built at DevHacks Season 3 (ASU, Oct 24-25 2026) for the Cubbie Hackathon, Track 1.

## Install

Text Cubbie this link and reply "yes":

```text
https://github.com/neric-joel/cubbie-callback
```

Then try one of these:

```text
I have an interview for this: https://boards.greenhouse.io/<company>/jobs/<id>
got a phone screen with Stripe for the software engineer intern role thursday
check my email for interview invites
mock interview me, tough mode
```

No keys and no sign-in. Gmail and Google Calendar make it smoother when they're connected, but they're optional.

## Why

Tech internships drew 273 applications per posting last year
([Handshake Internship Index 2025, via CNBC](https://www.nbcsandiego.com/news/business/money-report/applying-for-internships-is-nearly-twice-as-competitive-as-last-year-says-new-report-heres-why/3760360/)).
Getting the invite is the hard part, and then most prep is generic. Cubbie's own beta testers said so on
Oct 24: interview prep "was generic", and it should ask about the company and stack and check the inbox for the
invite. They also asked to see where Cubbie gets its facts, and said phone calls felt rude or hung up too early.
This Skill is built around those three complaints:

| Beta feedback | What Callback does |
| --- | --- |
| Prep is generic | Reads the real posting from the company's job board and builds the brief and questions from it |
| "Where did you get that?" | Every skill, fact and missed point cites a numbered line of the posting; the brief ends with the numbered posting |
| Calls feel rude or hang up | The call reads a fixed, calm script (chill or tough, never harsh) and the answer comes back as a voice note, so nothing depends on the call staying open |

## What the student sees

```text
Saguaro Pay interview Thu 2:00 PM. Your brief is ready.
Their posting leans on Kafka (3 mentions, line 14), then Java and Go.
Every point cites a line of the posting, so you can check it.
[brief.pdf]
Want a mock call tonight at 8? Chill or tough?
```

The phone rings at 8: *"This is your Saguaro Pay mock interview. Today I'll be a tough interviewer, so be specific.
A payment event gets delivered twice through a queue like Kafka. How do you make sure the customer is only charged
once?..."* The student answers in a voice note, and Cubbie replies:

```text
3/4 key points: strong.
Covered: delivery guarantees; store what was processed; offsets, acks or retries.
Missed: idempotency (a unique key per payment). The posting says "Make retries safe so a merchant is never charged twice when a message is delivered more than once." (line 7).
```

## How it works

```text
invite email / job link / "interview with X"
        |
        v
callback.py invite  ->  is it an invite, a scheduling request, an assessment or a rejection? any job links?
callback.py fetch   ->  public job-board API (no key) -> numbered plain-text lines, saved under HOME
        |                Greenhouse, Lever, Ashby, SmartRecruiters, Workday, or any page with schema.org JobPosting
        v
coach.extract       ->  81 skills matched on word boundaries, by section: requirement > part of the job > nice to have
                        (about-us, benefits and EEO text ignored); each with its best line and quote
callback.py brief   ->  Markdown brief -> Cubbie makes the PDF: top skills, practical facts (pay, office, start date,
                        graduation), what's NOT in the posting (ask your recruiter), 4 practice questions, pitch outline
callback.py question -> next question for the top skills (61 questions with answer key points), call script per tone
callback.py score   ->  checks the transcript for each key point, cites the posting line behind each miss, flags
                        filler words, too-short answers and "we" instead of "I"
```

The language model phrases things; it never decides what the posting says or what the answer covered. Those come
from the scripts, so the same answer always gets the same score and every claim can be checked against a line.

Cubbie abilities used, named in plain words in `SKILL.md`: Gmail search (optional), files and media (screenshots,
voice notes), web and browser (fallback for unusual career pages), documents (the PDF brief), Google Calendar (prep
blocks), reminders and phone calls, memory (tone preference), code sandbox.

## Reliability

- **Three ways in**: a job link, the invite email (Gmail, a forward or a screenshot), or just "interview with X".
- **Search by company + role**: picks the right posting when the same role is posted for several offices (prefers
  the student's location, else a US office) and says which office it used. If several different roles match, it asks.
- **Closed postings**: the first fetch is saved, so a posting that closes before the interview still works.
- **Anything else**: unreachable boards, JavaScript-only career pages and pasted text all go through
  `fetch --text-file`. Every failure has an exit code and a one-line instruction for Cubbie (see `SKILL.md`).
- **Consent**: calendar events, calls, check-ins and emails always wait for the student's yes.
- **No dependencies**: Python 3 standard library only, so nothing to install on Cubbie and nothing to break.
- **Tested**: 25 offline tests (`python -m unittest discover -s tests -v`) with fake responses from all six sources,
  closed and unreachable boards, ambiguous searches, invite classification and scoring; CI also runs the sample flow
  and the Cubbie kit's Skill validator. Live-checked on Oct 24 against Stripe, Ramp, Palantir and Intel postings.

## Try it without an interview

```bash
cd interview-callback
python3 scripts/callback.py fetch --sample
python3 scripts/callback.py brief --sample
python3 scripts/callback.py question --sample --tone tough
python3 scripts/callback.py score --sample
python3 scripts/callback.py fetch --company Stripe --role "Software Engineer Intern"   # live
```

`--sample` uses a made-up posting, invite and voice-note answer, and keeps its state apart from real data.

## Privacy

Gmail is searched only with a narrow query (`references/gmail-search.md`) and only matching threads are read. The
Skill saves a copy of the posting, the questions asked and the scores in `~/.config/interview-callback/` on the
student's own Cubbie drive; `forget --id <id>` deletes one interview. The only network calls go to public job boards.

## Limits

- Scoring checks whether an answer covers the key points, not how well it explains them; Cubbie adds one suggestion.
- Search by company name covers Greenhouse, Lever, Ashby and SmartRecruiters boards plus Intel's Workday; links work
  for any Workday site. Other career sites go through Cubbie's web reading.
- How long a phone call stays open is up to Cubbie, so the answer always comes back as a voice note or text.

## Layout

```text
interview-callback/          the Skill (this folder is what Cubbie installs)
  SKILL.md                   steps, replies, privacy, failure table
  scripts/callback.py        command line: fetch, brief, question, score, invite, list, forget
  scripts/boards.py          job-board fetchers, link parsing, search
  scripts/coach.py           extraction, brief, questions, scoring, invite classification
  assets/                    skills dictionary, question bank, sample posting, invite and answer
  references/gmail-search.md the Gmail query
tests/                       offline unit tests
```

MIT license.
