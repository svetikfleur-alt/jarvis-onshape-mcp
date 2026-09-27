"""Strict environment parsing for explicitly selected live tests."""

from dataclasses import dataclass
import os
import re
from typing import Optional


_SEGMENT = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")


class LiveConfigurationError(RuntimeError):
    """A fixed-code configuration error that never includes environment values."""


@dataclass(frozen=True)
class LiveConfig:
    access_key: str
    secret_key: str
    document_id: Optional[str]
    workspace_id: Optional[str]
    element_id: Optional[str]
    suite_budget: int


def load_live_config(*, require_model_ids: bool = True) -> LiveConfig:
    if os.getenv("JARVIS_LIVE_TESTS") != "1":
        raise LiveConfigurationError("LIVE_TESTS_NOT_ENABLED")
    primary = (os.getenv("ONSHAPE_ACCESS_KEY", ""), os.getenv("ONSHAPE_SECRET_KEY", ""))
    alternate = (os.getenv("ONSHAPE_API_KEY", ""), os.getenv("ONSHAPE_API_SECRET", ""))
    if all(primary) and all(alternate):
        raise LiveConfigurationError("LIVE_CREDENTIALS_AMBIGUOUS")
    if any(primary) != all(primary) or any(alternate) != all(alternate) or not (all(primary) or all(alternate)):
        raise LiveConfigurationError("LIVE_CREDENTIALS_INVALID")
    credentials = primary if all(primary) else alternate
    if require_model_ids:
        ids: tuple[Optional[str], Optional[str], Optional[str]] = tuple(
            os.getenv(name, "")
            for name in (
                "JARVIS_LIVE_DOCUMENT_ID",
                "JARVIS_LIVE_WORKSPACE_ID",
                "JARVIS_LIVE_ELEMENT_ID",
            )
        )
        if any(
            not isinstance(value, str)
            or not _SEGMENT.fullmatch(value)
            or value in {".", ".."}
            for value in ids
        ):
            raise LiveConfigurationError("LIVE_MODEL_IDS_INVALID")
    else:
        # A create-only canary must not receive or accidentally target ambient
        # identifiers for an existing user document.
        ids = (None, None, None)
    raw_budget = os.getenv("JARVIS_LIVE_SUITE_BUDGET", "30")
    try:
        suite_budget = int(raw_budget)
    except ValueError as error:
        raise LiveConfigurationError("LIVE_SUITE_BUDGET_INVALID") from error
    if suite_budget <= 0:
        raise LiveConfigurationError("LIVE_SUITE_BUDGET_INVALID")
    return LiveConfig(credentials[0], credentials[1], ids[0], ids[1], ids[2], suite_budget)
