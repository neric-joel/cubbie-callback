---
name: interview-callback
description: Preps a student for a specific job or internship interview from the real job posting, with a cited brief, prep time, and a mock interview by phone call or voice note that gets scored. Use when the student gets an interview invite or recruiter email, pastes a job link, says they have an interview coming up ("I have a Stripe interview Thursday", "got a phone screen with Intel"), asks for a mock interview or interview practice for a company or role, or wants to know what a job posting is really asking for.
---

# Interview Callback

The moment: an interview invite lands, and the student has a few days to prepare. Generic prep wastes those days.
This Skill pulls the real job posting, tells the student what it asks for most (every point cites a numbered line of
the posting, so nothing is invented), books prep time, and runs a mock interview by phone or text with a scorecard
that says exactly what the answer covered and what it missed.

No keys and no sign-in. Gmail and Google Calendar help when connected but are optional. The script uses only the
Python standard library, so there is nothing to install.

## When to use

- "I got an interview with Stripe!" / "phone screen with Intel next week"
- A forwarded or screenshotted interview invite, or a pasted job link
- "Can you do a mock interview for my Amazon SDE intern interview?" / "what does this posting actually want?"
- "Check my email for interview invites"

Not for: resumes or cover letters, emailing professors (Cubbie's own `email-to-professor` Skill), or general study
plans (`study-plan-builder`). An online assessment (HackerRank, CodeSignal) gets the brief and practice questions,
but no mock call.

## How to run the script

Run every command from this Skill's folder. Output is JSON; read `next` for what to do, and the exit code for
failures (table at the end). Each command takes `--id <id>` for a specific saved interview; without it, the most
recent one is used.

```bash
python3 scripts/callback.py fetch --url "<job link>" --when "<interview time as the student should see it>"
python3 scripts/callback.py fetch --company "<Company>" --role "<role title>" --location "<city or state>" --when "..."
python3 scripts/callback.py fetch --text-file posting.txt --company "<Company>" --role "<role>" --source-url "<link>"
python3 scripts/callback.py brief
python3 scripts/callback.py question --tone chill        # or tough; add --term "Kafka" to target one skill
python3 scripts/callback.py score --answer-file answer.txt
python3 scripts/callback.py invite --email-file invite.txt
python3 scripts/callback.py list                         # saved interviews, last scores, weak spots
python3 scripts/callback.py forget --id <id>             # delete one interview's saved data
```

## Steps

### 1. Find the interview

Use whatever the student gave you, in this order:

1. **A job link**: go to step 2 with `--url`.
2. **An invite** (forwarded text, a screenshot, or "check my email"): read it. For "check my email", search the
   student's Gmail with the query in `references/gmail-search.md` (add the company name when they gave one) and read
   only the matching threads. Save the email text to a file and run `invite --email-file <file>`, then follow its
   `category`:
   - `rejection`: not an invite. Don't start prep. Be kind and brief.
   - `assessment`: an online assessment. Offer the brief and practice questions, skip the mock call.
   - `schedule_request`: they still have to pick a time. Remind them, offer to draft the reply (send only after yes).
   - `interview` or `interview_confirmed`: use `job_links[0]` if there is one, else the company and role.
3. **Just a company** ("interview with Stripe Thursday"): if Gmail is connected, look for the invite as above;
   otherwise ask one question: "What's the role? A job link works best."

Read the interview date, time and time zone from the email yourself. Show times in the student's time zone and add
the original zone when it differs ("Thu 2:00 PM your time (1:00 PM PT)"). Arizona does not change clocks for
daylight saving. Pass the time to `fetch` or `brief` with `--when`.

If Gmail is not connected and the student asks you to check their email, say you can't see it yet, offer to connect
Gmail, and ask for the job link meanwhile.

### 2. Get the real posting

Run `fetch` (with `--url`, or `--company` and `--role`; add `--location` when you know the office). Then:

- `status: fetched`: go on.
- `status: matched`: the script found the posting by company and role. Tell the student which one you used, with the
  office and the link on its own line, so they can correct you: "Using this one, tell me if it's a different posting:".
  If there is a `location_note`, say it in one line.
- Exit 4, several possible postings: send up to three `candidates` (title and office, each link on its own line), ask
  which one, then run `fetch --url` with it.
- Exit 3, no posting found: search the web for the company's posting (prefer the company's careers site or a
  Greenhouse, Lever, Ashby, SmartRecruiters or Workday link) and run `fetch --url`. If nothing turns up, ask the
  student for the link or a screenshot.
- Exit 5, the job board can't be read: read the job page with the web ability (open it in the browser if the page
  needs JavaScript), save the job description text to a file, and run `fetch --text-file`.
- Exit 6, the posting is closed and nothing was saved: tell the student, and ask them to paste the description or
  send a screenshot. Read it and use `fetch --text-file`.
- `status: closed_using_saved_copy`: mention once that the posting closed and you're using the copy saved earlier.

Never fill in posting content yourself. If all you have is the invite email, say the brief will be general.

### 3. Send the brief

Run `brief`. Turn the Markdown file at `brief_path` into a one-page PDF (or a Google Doc if the student prefers),
keeping the line citations and the numbered posting at the end, because the numbers are how the student checks
every point. Send the `texts` as separate short messages, then the document.

### 4. Offer prep time (ask first)

Offer two 45-minute prep blocks before the interview. Check the student's Google Calendar for conflicts, propose
specific times ("Tue 7-7:45 pm and Wed 7-7:45 pm?"), and add them only after the student says yes. Title:
"Interview prep: <Company>". Description: the top three skills with their line numbers. If Calendar isn't connected,
offer reminders at those times instead.

### 5. Mock interview (ask first)

Ask one question at a time, with a default: "Want a mock interview? I can call you tonight at 8, or text the
questions. Chill or tough?" Remember the tone they pick for next time. Then run
`question --tone <chill|tough>` (add `--term "<skill>"` to practice one skill) and either:

- **Call**: create a one-time call at the time they picked. On the call, read `call_script` word for word in a calm,
  warm voice. "Tough" means harder questions, never a harsher voice: no yelling, no drill-sergeant lines. Let the
  student finish; never hang up while they are talking. The script ends by asking for the answer as a voice note
  after the call, so the mock works even when the call can't hold a conversation.
- **Text**: send `text_version`.

A scheduled call runs later on its own, so write it so it makes sense days from now, for example: "interview-callback
mock interview for <Company> (<id>): from the interview-callback Skill folder run `python3 scripts/callback.py question
--id <id> --tone <tone>`, call the student and read call_script."

### 6. Score the answer

When the answer arrives (a voice note, or text), transcribe the voice note, save the words to a file, and run
`score --answer-file <file>`. Send the `texts` as separate messages. Don't add points the scorecard didn't find; you
may add one concrete suggestion for the missed point, using the posting line it cites. Then offer the next question
("Another one tomorrow at 8?"). `question` returns exit 8 once every planned question was asked.

### 7. Right before the interview (ask first)

Offer a text check-in one hour before the interview with three short lines: the top skill to mention (with its
line), their weakest point so far (`list` → `weak_spots`), and one question to ask the interviewer from the brief's
"Not in the posting" list. Create it only after yes.

Afterwards, if the student wants, draft a short thank-you email to the recruiter in Gmail and send it only after they
say yes.

## Replies

- Short, plain text, one idea per message. No Markdown symbols, no tables.
- Lead with the answer. Cite the posting like "(line 14)".
- Links on their own line.
- One question at a time, with a default they can accept with "yes".
- The brief and the numbered posting go in the document, not the chat.
- No emoji reactions on bad news (a rejection, a low score).

Example:

```text
Saguaro Pay interview Thu 2:00 PM. Your brief is ready.
Their posting leans on Kafka (3 mentions, line 14), then Java and Go.
Every point cites a line of the posting, so you can check it.
Want a mock call tonight at 8? Chill or tough?
```

## Privacy

- Gmail: only the narrow search in `references/gmail-search.md`, only matching threads. Don't keep email text; the
  script keeps the job link, company, role and time.
- Saved data lives in `~/.config/interview-callback/` (a copy of the posting, questions asked, scores). `list` shows
  it; `forget --id <id>` deletes one interview. Nothing is sent anywhere except the request to the public job board.
- Calendar events, calls, check-ins and emails always wait for the student's yes. Cubbie texts only its own student.

## When something fails

| Exit | Meaning | What to do |
| --- | --- | --- |
| 2 | Missing or bad input (no link, nearly empty text, an answer of a few words) | Ask the student for the missing piece, one question |
| 3 | No posting found for that company | Search the web for the posting, then `fetch --url`; else ask for the link |
| 4 | Several postings could match | Send up to three candidates (links on their own lines) and ask which |
| 5 | The job board or page can't be read | Read the page with the web ability, save the text, `fetch --text-file` |
| 6 | Posting closed, no saved copy | Ask the student to paste the description or send a screenshot |
| 7 | No saved interview (or unknown id) | Run `list`; if empty, start at step 1 |
| 8 | Every planned question was asked | Offer a fresh round on one skill (`question --term`) or wrap up |

If the script fails some other way, tell the student in one line that the prep tool hit an error, and still help:
read the posting with the web ability and prep from that, saying the points aren't line-checked this time.

## Try it without a real interview

Every command takes `--sample`: a made-up "Saguaro Pay" posting, invite email and voice-note answer, kept apart from
the student's saved data and needing no network. `fetch --sample`, then `brief --sample`, `question --sample`,
`score --sample`. To try a real posting, paste any Greenhouse, Lever, Ashby, SmartRecruiters or Workday job link.

## Outside Cubbie

In Claude Code, Codex or another agent without calls or calendars, skip steps 4 and 7, ask the mock questions in
the chat, and score the typed answers the same way.
