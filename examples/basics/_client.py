"""Authenticated HTTP helpers shared by the basic examples."""

import os

import requests
from dotenv import load_dotenv


load_dotenv()


def rover_api_key() -> str:
    """Return the local rover-server key or fail before sending a request."""
    key = os.getenv("ROVER_API_KEY", "")
    if not key:
        raise RuntimeError(
            "ROVER_API_KEY is not set. Add the key printed by the SDK server "
            "to your environment or .env file."
        )
    return key


def rover_auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {rover_api_key()}"}


def rover_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(rover_auth_headers())
    return session
