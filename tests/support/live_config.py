"""Strict environment parsing for explicitly selected live tests."""

from dataclasses import dataclass
import os
import re


_SEGMENT = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")


class LiveConfigurationError(RuntimeError):
    """A fixed-code configuration error that never includes environment values."""


@dataclass(frozen=True)
class LiveConfig:
    access_key: str
    secret_key: str
    document_id: str
    workspace_id: str
    element_id: str
    suite_budget: int


def load_live_config() -> LiveConfig:
    if os.getenv("JARVIS_LIVE_TESTS") != "1":
        raise LiveConfigurationError("LIVE_TESTS_NOT_ENABLED")
    primary = (os.getenv("ONSHAPE_ACCESS_KEY", ""), os.getenv("ONSHAPE_SECRET_KEY", ""))
    alternate = (os.getenv("ONSHAPE_API_KEY", ""), os.getenv("ONSHAPE_API_SECRET", ""))
    if all(primary) and all(alternate):
        raise LiveConfigurationError("LIVE_CREDENTIALS_AMBIGUOUS")
    if any(primary) != all(primary) or any(alternate) != all(alternate) or not (all(primary) or all(alternate)):
        raise LiveConfigurationError("LIVE_CREDENTIALS_INVALID")
    credentials = primary if all(primary) else alternate
    ids = tuple(
        os.getenv(name, "")
        for name in (
            "JARVIS_LIVE_DOCUMENT_ID",
            "JARVIS_LIVE_WORKSPACE_ID",
            "JARVIS_LIVE_ELEMENT_ID",
        )
    )
    if any(not _SEGMENT.fullmatch(value) or value in {".", ".."} for value in ids):
        raise LiveConfigurationError("LIVE_MODEL_IDS_INVALID")
    raw_budget = os.getenv("JARVIS_LIVE_SUITE_BUDGET", "30")
    try:
        suite_budget = int(raw_budget)
    except ValueError as error:
        raise LiveConfigurationError("LIVE_SUITE_BUDGET_INVALID") from error
    if suite_budget <= 0:
        raise LiveConfigurationError("LIVE_SUITE_BUDGET_INVALID")
    return LiveConfig(credentials[0], credentials[1], ids[0], ids[1], ids[2], suite_budget)
