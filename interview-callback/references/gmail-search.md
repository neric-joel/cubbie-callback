# Finding the interview invite in Gmail

Search only when the student asks you to check their email, or mentions an interview without a link. Use this
query, and read only the threads it returns:

```text
newer_than:30d (subject:interview OR subject:"phone screen" OR subject:"technical screen" OR subject:"next steps" OR from:greenhouse.io OR from:greenhouse-mail.io OR from:hire.lever.co OR from:ashbyhq.com OR from:goodtime.io OR from:myworkday.com OR from:smartrecruiters.com OR from:calendly.com) -subject:(unfortunately OR "not moving forward" OR "update on your application") -category:promotions
```

- When the student named a company, add it at the end: `... -category:promotions stripe`.
- Skip newsletters, job alerts and Handshake digests (many postings, no invite).
- If several invites match, list them (company, role, date) and ask which one to prep for.
- For each candidate thread, save the newest message's text to a file and run
  `python3 scripts/callback.py invite --email-file <file>`; it says whether it is an invite, a request to pick a
  time, an online assessment or a rejection, and lists any job links in it.
- Keep nothing from the email beyond the company, role, interview time and job link.
