"""Loads predictor/.env (gitignored) into os.environ without extra dependencies."""
import os

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")


def load_env():
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def require(key):
    load_env()
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(
            f"{key} not set. Copy predictor/.env.example to predictor/.env and fill it in."
        )
    return value
