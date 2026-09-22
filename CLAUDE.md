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
