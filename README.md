# Callback: got the interview? Cubbie calls you first.

A [Cubbie](https://cubbie.ai) Skill. When an interview invite lands, Cubbie pulls the company's actual job posting,
texts you what it asks for most (quoting the posting, with line numbers you can check), sets up prep time, and runs
mock interview questions by phone call or text. Each answer gets a scorecard against what the posting asked for, and
a redo shows your progress ("2/4 last time, 4/4 now").

Built at DevHacks Season 3 (ASU, Oct 24-25 2026) for the Cubbie Hackathon, Track 1.

## Install

Text Cubbie this link and reply "yes":

```text
https://github.com/neric-joel/cubbie-callback
```

Then try one of these:

```text
I have an Amazon SDE intern interview next week
got the State Farm MAGNet internship interview at ASU on thursday
here's the posting for my interview: https://jobs.ashbyhq.com/<company>/<id>
check my email for interview invites
mock interview me, tough mode
```

No keys and no sign-in. Gmail, email events and Google Calendar make it smoother when connected, but are optional.

## Why

Tech internships drew 273 applications per posting last year
([Handshake Internship Index 2025, via CNBC](https://www.nbcsandiego.com/news/business/money-report/applying-for-internships-is-nearly-twice-as-competitive-as-last-year-says-new-report-heres-why/3760360/)).
Getting the invite is the hard part, and then most prep is generic. Cubbie's own beta testers said so on Oct 24:
interview prep "was generic", and it should ask about the company and stack and check the inbox for the invite. They
also asked to see where Cubbie gets its facts, and said phone calls felt rude or hung up too early.

| Beta feedback | What Callback does |
| --- | --- |
| Prep is generic | Reads the real posting from the company's job board; the brief, questions and scores all come from it |
| "Where did you get that?" | Every skill, fact and missed point quotes the posting with its line number; the brief ends with the numbered posting |
| Calls feel rude or hang up | The call reads a fixed, calm script (tough means harder questions, never a harsher voice), and the answer comes back as a voice note, so nothing depends on the call staying open |
| Wants the invite found in the inbox | Narrow, opt-in search of email events and Gmail; tells an invite from a scheduling request, an online assessment or a rejection |

## Tested on a real Cubbie, Oct 24 2026

From the iMessage thread (texted from a real phone, nothing staged):

```text
me:     https://github.com/neric-joel/cubbie-callback/tree/main/interview-callback
Cubbie: This is an interview-prep skill from neric-joel/cubbie-callback. It builds prep briefs from job postings and
        scores mock answers; its instructions say it needs no keys or extra packages. Want me to install it?
me:     yes
Cubbie: Installed interview-callback. No extra packages or sign-in were needed.
me:     I just got an Amazon SDE intern interview for next Thursday at 2pm!! can you help me prep?
Cubbie: Your prep brief is ready. Focus on data structures, algorithms, object-oriented design, and your strongest
        coding language (posting lines 67-68), plus clear project-ownership stories.
        I used Amazon's Summer 2027 USA posting [link]. I couldn't find the invite, so Thursday, Oct 29 at 2 PM is
        presumed Arizona time.
        Your calendar looks free at these times. Want me to book Tue Oct 27 and Wed Oct 28, 7-7:45 PM prep, schedule a
        relaxed mock call tonight at 8 PM, and a Thursday 1 PM text reminder? You can say "tough" for a harder mock.
        [brief.pdf]
me:     skip the calendar for now. tough mode - text me a mock question right now
Cubbie: Tough mode. How would you design the classes for a parking garage? Name the main objects and defend one
        design choice.
me:     [answer: ParkingGarage, Levels, ParkingSpots, a Vehicle base class, park() issues a Ticket...]
Cubbie: 4/4 in substance. Your class hierarchy and park/exit flow are clear. The scorer missed your design principle,
        but keeping spot assignment in Level demonstrates encapsulation and separation of responsibilities - that counts.
        Tough follow-up: two cars simultaneously select the last available spot. How do you prevent both from getting it?
me:     call me in 1 minute and ask me the next mock question out loud
Cubbie: Set for 3:06 PM Arizona time. I'll call with the next mock question in tough mode.
        [the phone rang with the question; a voice note followed]
```

What we changed after it: the call showed up as "Scam Likely", so the Skill now tells students to save Cubbie's
number first; and the "that counts" credit is now recorded (`score --credit`) so redos and weak spots agree.

## What the student sees

```text
Saguaro Pay interview Thu Oct 29, 2:00 PM. Your prep brief is ready.
Top of their list: Kafka and Distributed systems. In their words: "Curiosity about distributed systems and how Kafka-style event streaming works."
Then Java, Go and Python; Data structures and algorithms; SQL and Testing.
The brief quotes the posting for every point, with line numbers so you can check it.
[brief.pdf]
Want me to set up your prep?
- 2 prep blocks: Tue and Wed 7-7:45 pm
- a mock interview call tonight at 8
- a text 1 hour before the interview
```

The phone rings at 8: *"This is your Saguaro Pay mock interview. Today I'll be a tough interviewer, so be specific.
A payment event gets delivered twice through a queue like Kafka. How do you make sure the customer is only charged
once? Give me one real example. When we hang up, send your answer as a voice note..."* The student answers, and:

```text
2/4 key points: solid.
Covered: delivery guarantees; offsets, acks or retries.
Missed: idempotency (a unique key per payment); store what was processed. The posting says "Make retries safe so a merchant is never charged twice when a message is delivered more than once." (line 7).
Follow-up: you didn't cover idempotency (a unique key per payment). How would you handle that here?
```

The next evening, a redo: `2/4 last time, 4/4 now: strong.`

(Saguaro Pay is the made-up sample posting bundled with the Skill.)

## Works with the companies ASU students interview with

Live-checked on Oct 24 2026 by company name and role:

| Company | Found | Source |
| --- | --- | --- |
| Amazon | Software Development Engineer Intern - Summer 2027 (USA), Seattle | amazon.jobs API |
| American Express | 2027 Software Engineer internship, Phoenix AZ | Oracle Recruiting |
| State Farm | Summer 2027 Intern - MAGNet Program @ Arizona State University, Tempe | jobs.statefarm.com API |
| RTX | Software Engineer Intern, Tucson AZ | Workday (intern filter) |
| NVIDIA | NVIDIA 2027 Internships: Software Engineering, Santa Clara | Workday (intern filter) |
| Salesforce | Summer 2027 Intern - Software Engineer, San Francisco and more | Workday |
| Honeywell | Software Engineer Co-Op - Summer 2027 | Oracle Recruiting |
| Microsoft, Northrop Grumman | asks which of the open software intern roles | Eightfold, Workday |
| Lockheed Martin | Software Engineer - Intern, Marietta GA | Eightfold |
| Stripe, Palantir, Ramp, Databricks | the matching intern posting, US office | Greenhouse, Lever, Ashby |
| Boeing, Carvana, Wells Fargo | says there's no open software intern posting right now, and shows the closest | Workday, Greenhouse |

Also searchable by name: Intel, PayPal, Capital One, Micron, Dexcom (Workday), JPMorgan (Oracle), and any company on
Greenhouse, Lever, Ashby or SmartRecruiters. Any job link works if the page publishes standard job data; for the
rest (Google, Apple, Meta, Handshake) Cubbie reads the page or a screenshot and passes the text in.

It doesn't guess: if an intern search finds only full-time jobs, it says there's no open intern posting and shows
the closest ones; if the same role is posted in several offices, it picks the student's place (else a US office) and
says which; if several different roles match, it asks.

## How it works

```text
invite email / job link / "interview with X"
        |
        v
callback.py invite   is it an invite, a scheduling request, an assessment or a rejection? any job links?
callback.py fetch    public job-board API (no key) -> numbered plain-text lines, saved under HOME
        |            Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Eightfold, Oracle, amazon.jobs, State Farm,
        |            or any page with schema.org JobPosting data
        v
coach.extract        81 skills matched by section: requirement > part of the job > nice to have; about-us,
                     benefits and equal-opportunity text ignored; "security clearance" isn't a security skill
callback.py brief    Markdown brief -> Cubbie makes the PDF: top skills with quotes, practical facts (pay, office,
                     start date, graduation, citizenship), what's NOT in the posting (ask your recruiter),
                     practice questions, a pitch outline, the numbered posting
callback.py question two questions per top skill (a technical one and "walk me through a project"), plus company
                     and behavioral ones; call script per tone; remembers which question is waiting for an answer
callback.py score    checks the transcript for each key point (ignoring "I don't know X"), quotes the posting line
                     behind each miss, flags filler words, short answers and "we" instead of "I"; redos show progress
```

The language model phrases things; it never decides what the posting says or what the answer covered. Those come
from the scripts, so the same answer always gets the same score and every claim can be checked against a line.

Cubbie abilities used, named in plain words in `SKILL.md`: email events and Gmail search (optional), files and media
(screenshots, voice notes), web and browser (fallback for other career pages), documents (the PDF brief), Google
Calendar (prep blocks), reminders and phone calls (mock interviews, a 60-second pep call), memory (tone), code sandbox.

## Reliability

- **No dependencies**: Python 3 standard library only, so nothing to install on Cubbie and nothing to break.
- **Every failure has a next step**: exit codes 1-8 each map to one line of instructions in `SKILL.md`; the output is
  always valid JSON under Cubbie's 8,000-character read limit, never a traceback.
- **Safe with untrusted input**: links into private networks are refused (including after redirects), downloads are
  capped, every run has a 100-second budget, saved files can't escape the Skill's folder, and two runs can't
  overwrite each other.
- **Closed postings**: the first fetch is saved, so a posting that closes before the interview still works.
- **Consent**: calendar events, calls, check-ins and emails always wait for the student's yes. "Stop" and "remove"
  cancel this Skill's scheduled calls first.
- **Tested**: 59 offline tests (`python -m unittest discover -s tests -v`) with fake responses from every source,
  closed, unreachable, rate-limited and hostile pages, ambiguous and intern-less searches, invite classification and
  scoring. CI also runs the sample flow and the Cubbie kit's Skill validator. Reviewed by three judge agents (a Cubbie
  engineer, a security and bug hunter, and a student), and their findings fixed.

## Try it without an interview

```bash
cd interview-callback
python3 scripts/callback.py fetch --sample
python3 scripts/callback.py brief --sample
python3 scripts/callback.py question --sample --tone tough
python3 scripts/callback.py score --sample
python3 scripts/callback.py question --sample --retry
python3 scripts/callback.py fetch --company Amazon --role "SDE intern"   # live
```

`--sample` uses a made-up posting, invite and voice-note answer, and keeps its state apart from real data.

## Privacy

Email is searched only with a narrow query (`references/gmail-search.md`) and only matching threads are read. The
Skill saves a copy of the posting, the questions asked and the scores in `~/.config/interview-callback/` on the
student's own Cubbie drive; `forget --id <id>` or `forget --all` deletes them. The only network calls go to public
job boards.

## Limits

- Scoring checks whether an answer covers the key points, not how well it explains them; Cubbie credits a point made
  in other words and adds one suggestion.
- Google, Apple, Meta and Handshake pages need Cubbie's web reading (or a screenshot) instead of a direct fetch.
- How long a phone call stays open is up to Cubbie, so the answer always comes back as a voice note or text.

## Layout

```text
interview-callback/          the Skill (this folder is what Cubbie installs)
  SKILL.md                   steps, replies, privacy, stop/remove, failure table
  scripts/callback.py        command line: fetch, brief, question, score, invite, list, forget
  scripts/boards.py          job-board fetchers, link parsing, search, safe HTTP
  scripts/coach.py           extraction, brief, questions, scoring, invite classification
  assets/                    skills dictionary, question bank, sample posting, invite and answer
  references/gmail-search.md the email query
tests/                       offline unit tests
```

MIT license.
