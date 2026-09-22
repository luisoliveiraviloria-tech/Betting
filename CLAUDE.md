# UK/IRE Horse Racing — Betting Pipeline

Quant pipeline for UK & Ireland horse racing: `racing/` builds features/model/staking
from the archive, `quant/` handles Betfair auth and market data. See `racing/README.md`
and `quant/README.md` for the architecture.

## Pending tasks

- [ ] **Finish Betfair cert (bot) login setup, then confirm `racing.exchange` works.**
  Interactive login (`login_interactive()`) is blocked by Betfair's Cloudflare edge from
  this session's cloud IP (403, HTML challenge page — confirmed live 2026-09-22, not a
  credentials issue). Cert login is the fix. State as of 2026-09-22:
  - A 5-year self-signed cert/key pair was generated (`betfair-bot.crt` / `.key`,
    RSA 2048, expires 2031-09-21) and delivered to the user via SendUserFile
    (not committed to the repo — regenerate with the command below if lost, since the
    container is ephemeral and local files don't survive a session reset).
  - Code is wired and already pushed: `quant/betfair_auth.py` has
    `login_cert_from_env()`, which reads `BETFAIR_CLIENT_CERT_B64` /
    `BETFAIR_CLIENT_KEY_B64` from the environment (not files on disk), decodes them to
    a 0600 temp file, and calls the existing `login_cert()`. `racing/exchange.py`'s
    `login()` now tries, in order: explicit `--cert`/`--key` args → the env-var pair →
    interactive.
  - **Still needed from the user** (blocked on train WiFi 2026-09-22, resuming on
    laptop): (1) log into betfair.com in a real browser (needs their login + likely
    2FA, cannot be done from this session) → My Account → API Access → Automated
    Betting Program → upload the public `.crt`. (2) Add `BETFAIR_CLIENT_CERT_B64` and
    `BETFAIR_CLIENT_KEY_B64` as persistent env vars in this environment's settings
    (same place `BETFAIR_USERNAME`/`PASSWORD`/`APP_KEY` already live) — base64 forms
    were sent to the user as files (`betfair-bot.key.b64` etc.); regenerate with
    `openssl req -x509 -newkey rsa:2048 -keyout betfair-bot.key -out betfair-bot.crt
    -days 1825 -nodes -subj "/CN=betfair-bot"` then `base64 -w0` each file if those
    were lost too.
  - **Once both env vars are set:** run `python -m racing.exchange --date
    $(date +%Y-%m-%d)` with no flags. This has never been tested end-to-end — cert
    login itself (as opposed to interactive) has not been confirmed to clear
    Betfair's edge from this account/IP, only the code path has been built.
  - Context: this unblocks live/delayed WIN market prices for UK/IRE racing, which
    `racing/daily.py --prices` needs for a live (non-BSP-replay) day. Free racecard
    data (no auth needed) was separately built by scraping Racing Post — see chat
    history around 2026-09-22 for the full runner/form/trainer/OR/draw dataset if a
    dashboard artifact needs rebuilding (published to
    https://claude.ai/artifact/Vk4hPhNN7aukiAZbxx2ijV, but that link needs the artifact
    owner's account to reopen — not fetchable fresh from a new session without
    re-scraping).

- [ ] **Security follow-up:** `BETFAIR_PASSWORD` was accidentally printed to a tool
  output during debugging on 2026-09-22 (`env | grep`). Recommend the user rotate it
  independent of the cert work above (cert login won't need the password at all once
  it's confirmed working — `login_cert()` still takes username/password as form fields
  per Betfair's API, so it's not fully retired, but reduces reliance on it).

- [ ] **New finding, 2026-09-22 18:15:** `racing/bsp_data.py::fetch_one` also returns
  `error:http403` from this session's cloud IP (tried `ire`/`win`/2026-09-22 directly).
  This means the block isn't limited to `identitysso.betfair.com` (interactive login) —
  `promo.betfair.com`'s free, no-auth CSV endpoint is blocked from this sandbox too. So
  BSP backfill/backtesting, not just live exchange prices, needs to run from a
  residential IP (or wherever the cert-login fix ends up running) — don't assume
  `bsp_data.py` "just works" from this container without checking first. Working
  fallback used today for reading results (not prices) from this sandbox:
  `sportinglife.com/racing/meeting/{date}/{course}/{meeting_id}` — find the
  `meeting_id` via a WebFetch of `sportinglife.com/racing/results` first, since it's
  not derivable from the Racing Post course ID.

- [ ] **The bigger project (flagged by the user 2026-09-22): turn today's manual
  handicapping pass into a repeatable, logged validation loop.** Today was a one-off,
  done by hand in chat: scraped Listowel's card, ranked runners with an ad hoc
  `softmax(OR/8)` heuristic (no market data, no trained model — see "Lessons learned"
  below), then checked results manually via sportinglife.com at end of day. That's not
  reusable or measurable at scale. The actual project this should become:
  1. A script (`racing/live_card.py`? — doesn't exist yet) that pulls today's
     runners/OR/form the way the Listowel scrape did, but saves it as structured data
     (CSV/SQLite row per runner per race) instead of a one-off HTML artifact.
  2. Route it through the **real** trained model (`racing/model.py` /
     `racing/daily.py`) via `racing/live.py::resolve_identities()` instead of the toy
     OR-softmax — the toy heuristic was a stand-in because wiring live-card data into
     the trained model wasn't done today, not because it's the right long-term method
     (see lesson 1 below).
  3. A results-logging step (`racing/bsp_data.py` once its IP-block is sorted, or the
     sportinglife.com fallback) that joins actual finishing order back onto the saved
     predictions automatically, per race, every day — not a manual end-of-day check.
  4. Only once that loop has run over many race days does a real strike rate / CLV
     number mean anything — see the n=6 sample-size caveat below. One day proves
     nothing either way.

## Lessons learned — 2026-09-22 Listowel manual validation

Six races (14:22–17:16) were ranked by hand using `softmax(OR/8)` — a heuristic, not
the trained model — with no market prices available. Results were checked end of day
against actual finishers. n=6, so none of this is a statistically meaningful strike
rate; it's a set of specific, checkable misses worth understanding.

- **The winner was my bottom-rated or unranked horse in 3 of 6 races** (14:22 —
  Platino Bianco won at 18/1 off the *lowest* OR in the field; 16:07 — Letmeentertainyou
  won at 25/1 from mid-pack while the "clean signal, skip the screenshot" pick Romance
  ran 2nd; 16:41 — Highbury See See ran 2nd off the field's lowest OR, and was
  **the actual market favourite** at 5/2, which the OR-only heuristic had no way to see).
  This isn't just noise — it's the expected structural failure mode of ranking by OR
  alone: the official handicapper's whole job is to assign weight so that OR stops
  discriminating winners in a truly competitive field. Once that's done, the remaining
  edge has to come from what OR *doesn't* capture — recent-form trajectory, market
  money, draw/pace, trainer intent — which is exactly why `racing/model.py` exists
  instead of just sorting by OR. Don't reach for the OR-softmax shortcut again as
  anything more than a last-resort placeholder when there's no time to run the real
  model.
- **The market, where available, was still the best single signal.** The one race with
  real Betfair prices (13:47) correctly had the winner as the clear favourite, and the
  actual runner-up was the market's #3 fair-price pick — closer to right than any
  ratings-only read managed in the other six. Reinforces the standing project stance:
  wherever prices exist, they beat a ratings/form heuristic; the heuristic is only a
  fallback for when they don't.
- **The "recent form overrides a stale OR" call went both ways — it isn't a reliable
  rule on its own.** In 14:22, flagging a low-OR last-time winner (She's On The Ball)
  paid off — it ran 2nd. In 15:32, downgrading a higher-OR horse (Meriden) off one flat
  run backfired — it won, and my top OR pick (Jagged Edge) which I trusted over it only
  ran 3rd. One data point each way. The actual lesson isn't "trust form over rating" or
  vice versa — it's that a single-run dip is uninterpretable without knowing *why*
  (trip, ground, a freshening run) and I don't have that context from form figures
  alone. Don't state either direction with confidence off one line of form.
- **Confidence language should track sample size, not how clean the signal looks.**
  16:07 (Romance) was the one race I called "skip the screenshot, fairly confident" —
  rating and recent form both pointed the same way. It lost to a 25/1 shot. Two
  agreeing signals in a 7-runner field is still a small sample; say so next time
  instead of downgrading the hedge because the read felt clean.
