"""
Betfair session authentication.

Two login flows, per Betfair's Identity SSO API (developer.betfair.com,
"Non-Interactive (Bot) Login" vs "Interactive Login"):

  - `login_interactive()` — username/password + app key, POST to
    identitysso.betfair.com/api/login. Simplest, but Betfair's edge
    (Cloudflare) blocks this from most datacenter/cloud IPs with a 403
    HTML challenge page, regardless of credentials. Confirmed blocked
    from this sandbox on 2026-09-21 — run this from a residential IP
    (the user's own machine / RunPod with a home connection) instead.

  - `login_cert()` — the "bot login" flow Betfair recommends for
    unattended/automated applications: a self-signed client SSL
    certificate (public half uploaded to the account under My Account >
    API Keys) is presented to identitysso-cert.betfair.com/api/certlogin.
    Less prone to the Cloudflare block above, but requires the cert to
    be generated and registered first — not yet done for this account.

Either flow returns a `sessionToken` ("ssoid") that must be sent as the
`X-Authentication` header on every subsequent API-NG / Historic Data call,
alongside `X-Application: <app key>`. Session tokens expire after a period
of inactivity (Betfair docs: ~4-24h depending on product) and must be
re-requested, not refreshed.
"""
import os
from dataclasses import dataclass

import requests

INTERACTIVE_LOGIN_URL = "https://identitysso.betfair.com/api/login"
CERT_LOGIN_URL = "https://identitysso-cert.betfair.com/api/certlogin"


@dataclass
class BetfairSession:
    session_token: str
    app_key: str

    @property
    def headers(self) -> dict:
        return {
            "X-Application": self.app_key,
            "X-Authentication": self.session_token,
            "Accept": "application/json",
        }


class BetfairLoginError(RuntimeError):
    pass


def _creds_from_env(username: str | None, password: str | None, app_key: str | None):
    username = username or os.environ.get("BETFAIR_USERNAME")
    password = password or os.environ.get("BETFAIR_PASSWORD")
    app_key = app_key or os.environ.get("BETFAIR_APP_KEY")
    if not all([username, password, app_key]):
        raise BetfairLoginError(
            "Missing credentials: set BETFAIR_USERNAME, BETFAIR_PASSWORD, "
            "BETFAIR_APP_KEY (env or .env) or pass them explicitly."
        )
    return username, password, app_key


def login_interactive(username: str | None = None, password: str | None = None,
                       app_key: str | None = None, timeout: int = 20) -> BetfairSession:
    """Non-cert login. Works from a browser-like / residential IP only —
    see module docstring for the Cloudflare block observed from cloud IPs."""
    username, password, app_key = _creds_from_env(username, password, app_key)
    headers = {
        "X-Application": app_key,
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    resp = requests.post(
        INTERACTIVE_LOGIN_URL, headers=headers,
        data={"username": username, "password": password}, timeout=timeout,
    )
    return _parse_login_response(resp, app_key)


def login_cert(cert_path: str, key_path: str, username: str | None = None,
               password: str | None = None, app_key: str | None = None,
               timeout: int = 20) -> BetfairSession:
    """Cert-based ("bot") login. `cert_path`/`key_path` are the client
    certificate registered under My Account > API Keys for this app key."""
    username, password, app_key = _creds_from_env(username, password, app_key)
    headers = {
        "X-Application": app_key,
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    resp = requests.post(
        CERT_LOGIN_URL, headers=headers,
        data={"username": username, "password": password},
        cert=(cert_path, key_path), timeout=timeout,
    )
    return _parse_login_response(resp, app_key, cert_flow=True)


def _parse_login_response(resp: requests.Response, app_key: str, cert_flow: bool = False) -> BetfairSession:
    if resp.status_code != 200 or "application/json" not in resp.headers.get("Content-Type", ""):
        raise BetfairLoginError(
            f"Login failed: HTTP {resp.status_code}, non-JSON response "
            f"(first 200 chars: {resp.text[:200]!r}). This is the signature "
            f"of Betfair's Cloudflare edge blocking the request before it "
            f"reaches the login service (common from cloud/datacenter IPs) "
            f"rather than a credentials problem — retry from a residential "
            f"IP, or switch to login_cert()."
        )
    body = resp.json()
    # Response key names per Betfair Identity SSO API docs
    # (developer.betfair.com/en/get-started/#getting-started-header-1):
    # interactive login -> {"token","status"}; cert login -> {"sessionToken","loginStatus"}.
    token_key = "sessionToken" if cert_flow else "token"
    status_key = "loginStatus" if cert_flow else "status"
    if body.get(status_key) not in ("SUCCESS",) or not body.get(token_key):
        raise BetfairLoginError(f"Login rejected: {body.get(status_key)} ({body})")
    return BetfairSession(session_token=body[token_key], app_key=app_key)
