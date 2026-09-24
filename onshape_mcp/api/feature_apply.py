"""Apply a feature (create or update) and return a structured result including
the real Onshape `featureStatus`.

Fixes the #1 starter bug: every mutating tool currently returns "success" text
even when Onshape's response body says `featureState.featureStatus == "ERROR"`.
Routing every feature mutation through `apply_feature_and_check` gives callers
(and the LLM layer) a reliable signal of whether the feature actually built.

Evidence for the response shape used here is captured in
`scratchpad/smoke-test.md` and `scratchpad/probe-patch-and-shadedviews.md`
in the parent project (`/Users/shef/projects/onshape-mcp/`).
"""

from __future__ import annotations

import copy
import math
from typing import Any, Dict, List, Literal, Optional

from loguru import logger
from pydantic import BaseModel, Field

from .client import OnshapeClient
from .request_guard import safe_exception_message


FeatureStatus = Literal["OK", "INFO", "WARNING", "ERROR", "UNKNOWN"]
MutationVerification = Literal["verified", "unverified", "no_effect", "failed"]


class MutationPreflightError(ValueError):
    """Mutation input was rejected before any delegated write."""


class FeatureApplyResult(BaseModel):
    """Structured result of applying (create/update) a feature.

    `ok` is True only when transport, regeneration, and requested-state
    verification all succeeded. HTTP acceptance alone is not CAD success.

    `changes` (when set) is a git-diff-style summary of what the feature
    altered in the part — volume delta, faces added/removed, bbox change,
    anomalies. Only populated when the caller passed `track_changes=True`.
    """

    ok: bool
    status: FeatureStatus
    feature_id: str
    feature_name: str
    feature_type: str
    error_message: Optional[str] = None
    changes: Optional[Dict[str, Any]] = None
    transport_ok: Optional[bool] = None
    http_ok: Optional[bool] = None
    regen_ok: Optional[bool] = None
    mutation_verification: MutationVerification = "unverified"
    changed: Optional[bool] = None
    verification_scope: str = "none"
    reason_code: Optional[str] = None
    verification_message: Optional[str] = None
    # Structured HTTP diagnostics cross the MCP boundary only through the
    # dedicated sanitized exception projection, never as an arbitrary dict.
    diagnostic: Optional[Dict[str, Any]] = Field(
        default=None, exclude=True, repr=False
    )
    # Compatibility/evidence field for internal callers only. Pydantic's
    # exclusion is a second boundary behind the explicit MCP projection.
    raw: Dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)

    def public_dict(self) -> Dict[str, Any]:
        """Bounded allowlisted representation safe for MCP mutation results."""

        semantic_ok = (
            self.ok
            and self.transport_ok is True
            and self.http_ok is True
            and self.regen_ok is True
            and self.mutation_verification == "verified"
        )
        return {
            "ok": semantic_ok,
            "status": self.status,
            "feature_id": self.feature_id,
            "feature_type": self.feature_type,
            "feature_name": self.feature_name,
            "error_message": self.error_message,
            "transport_ok": self.transport_ok,
            "http_ok": self.http_ok,
            "regen_ok": self.regen_ok,
            "mutation_verification": self.mutation_verification,
            "changed": self.changed,
            "verification_scope": self.verification_scope,
            "reason_code": self.reason_code,
            "verification_message": self.verification_message,
        }


def _normalize_status(raw_status: Any) -> FeatureStatus:
    return (
        raw_status
        if raw_status in ("OK", "INFO", "WARNING", "ERROR")
        else "UNKNOWN"
    )


def _regen_ok(status: FeatureStatus) -> bool:
    return status in ("OK", "INFO")


def _find_feature(features_doc: Dict[str, Any], feature_id: str) -> Optional[Dict[str, Any]]:
    for feature in features_doc.get("features") or []:
        if isinstance(feature, dict) and feature.get("featureId") == feature_id:
            return feature
    return None


def _feature_state(features_doc: Dict[str, Any], feature_id: str) -> Dict[str, Any]:
    state = (features_doc.get("featureStates") or {}).get(feature_id)
    return state if isinstance(state, dict) else {}


def _scalar_equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(float(expected), float(actual), rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip() == actual.strip()
    return expected == actual


def _requested_subset_compare(
    expected: Any,
    actual: Any,
    *,
    field_name: Optional[str] = None,
    parent_bt_type: Optional[str] = None,
) -> Optional[bool]:
    """Compare requested wire fields without requiring server-added metadata.

    ``None`` means the response omitted/re-shaped a requested field, so exact
    comparison would overclaim in the presence of Onshape canonicalization.
    """

    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return None
        current_bt_type = (
            expected.get("btType")
            if isinstance(expected.get("btType"), str)
            else parent_bt_type
        )
        for key, expected_value in expected.items():
            if key in {"featureId", "nodeId"}:
                continue
            if key not in actual:
                return None
            compared = _requested_subset_compare(
                expected_value,
                actual[key],
                field_name=key,
                parent_bt_type=current_bt_type,
            )
            if compared is not True:
                return compared
        return True
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return None
        if expected and all(isinstance(item, dict) and item.get("parameterId") for item in expected):
            by_id = {
                item.get("parameterId"): item
                for item in actual
                if isinstance(item, dict) and item.get("parameterId")
            }
            for item in expected:
                candidate = by_id.get(item["parameterId"])
                if candidate is None:
                    return None
                compared = _requested_subset_compare(
                    item, candidate, parent_bt_type=parent_bt_type
                )
                if compared is not True:
                    return compared
            return True
        if len(expected) != len(actual):
            return None
        for expected_item, actual_item in zip(expected, actual):
            compared = _requested_subset_compare(
                expected_item,
                actual_item,
                field_name=field_name,
                parent_bt_type=parent_bt_type,
            )
            if compared is not True:
                return compared
        return True
    if _scalar_equal(expected, actual):
        return True
    # Onshape may normalize equivalent expressions and query text on write.
    # A textual mismatch in those fields is not evidence of an ineffective
    # mutation unless an equivalence evaluator is available.
    if field_name in {"expression", "queryString", "queryStatement"}:
        return None
    return False


def _requested_update_compare(
    update: Dict[str, Any], parameter: Dict[str, Any]
) -> Optional[bool]:
    for key, expected in update.items():
        if key == "parameterId":
            continue
        if key not in parameter:
            return None
        if not _scalar_equal(expected, parameter[key]):
            # Quantity expressions are frequently canonicalized into another
            # equivalent representation. A mismatch is therefore ambiguous,
            # while exact equality is safe to verify.
            if key == "expression":
                return None
            return False
    return True


async def apply_feature_and_check(
    client: OnshapeClient,
    document_id: str,
    workspace_id: str,
    element_id: str,
    feature_payload: Dict[str, Any],
    *,
    operation: Literal["create", "update"] = "create",
    feature_id: Optional[str] = None,
    track_changes: bool = False,
    verify_requested_state: bool = True,
) -> FeatureApplyResult:
    """Apply a feature to a Part Studio and return its Onshape-reported status.

    Args:
        client: Active OnshapeClient (reused, not closed here).
        document_id: Onshape document id.
        workspace_id: Onshape workspace id.
        element_id: Part Studio element id.
        feature_payload: Body to POST. Typically
            `{"feature": {...}, "serializationVersion": ..., "sourceMicroversion": ...}`.
            The starter's existing builders return just the inner feature dict; callers
            can wrap it as `{"feature": feature_dict}` before calling.
        operation: "create" (POST /features) or "update"
            (POST /features/featureid/{feature_id}).
        feature_id: Required when `operation="update"`.

    Returns:
        FeatureApplyResult with the real featureStatus, never "unknown" feature_id,
        and `error_message` populated whenever status is non-OK.

    Raises:
        ValueError: operation="update" without feature_id.
        httpx.HTTPStatusError: on HTTP 4xx/5xx (malformed request, auth, etc.).
            NOT raised for HTTP 200 responses carrying an ERROR featureStatus —
            those flow through as structured results.
    """

    if operation == "update" and not feature_id:
        raise ValueError("feature_id is required when operation='update'")
    if operation not in {"create", "update"}:
        raise ValueError(f"operation must be 'create' or 'update', got {operation!r}")

    base = (
        f"/api/v9/partstudios/d/{document_id}/w/{workspace_id}/e/{element_id}/features"
    )
    path = base if operation == "create" else f"{base}/featureid/{feature_id}"

    # Snapshot bodies before the feature if caller wants a git-diff-style
    # `changes` block. Failures to snapshot don't block the feature apply —
    # we just skip the diff and log.
    bodies_before = None
    mass_before: Optional[Dict[str, Any]] = None
    if track_changes:
        try:
            bd = await client.get(
                f"/api/v9/partstudios/d/{document_id}/w/{workspace_id}/e/{element_id}/bodydetails"
            )
            bodies_before = bd.get("bodies") or []
            mass_before = await client.get(
                f"/api/v9/partstudios/d/{document_id}/w/{workspace_id}/e/{element_id}/massproperties"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "track_changes: before-snapshot failed ({}); skipping diff",
                safe_exception_message(e),
            )
            bodies_before = None

    response = await client.post(path, data=feature_payload)

    state = response.get("featureState") if isinstance(response, dict) else None
    feature = response.get("feature", {}) if isinstance(response, dict) else {}
    feature = feature if isinstance(feature, dict) else {}
    real_feature_id = feature.get("featureId") or feature_id or ""
    feature_name = feature.get("name", "")
    feature_type = feature.get("featureType") or feature.get("btType", "")
    status = _normalize_status((state or {}).get("featureStatus", "UNKNOWN"))

    # A regeneration failure is conclusive without a reread: transport worked,
    # but the requested CAD state did not build.
    if not _regen_ok(status) and status != "UNKNOWN":
        fs_status = (
            await _fetch_feature_status_enum(
                client, document_id, workspace_id, element_id, real_feature_id
            )
            if real_feature_id
            else None
        )
        return FeatureApplyResult(
            ok=False,
            status=status,
            feature_id=real_feature_id,
            feature_name=feature_name,
            feature_type=feature_type,
            error_message=_extract_error_message(state or {}, fs_status=fs_status),
            transport_ok=True,
            http_ok=True,
            regen_ok=False,
            mutation_verification="failed",
            changed=False,
            verification_scope="feature_state",
            reason_code="FEATURE_REGENERATION_ERROR",
            verification_message="Onshape reported a non-success regeneration status.",
            raw={"response": response if isinstance(response, dict) else {}},
        )

    try:
        features_after = await client.get(base)
    except Exception as error:  # noqa: BLE001
        logger.warning(
            "apply_feature_and_check: authoritative feature reread failed ({})",
            type(error).__name__,
        )
        return FeatureApplyResult(
            ok=False,
            status=status,
            feature_id=real_feature_id,
            feature_name=feature_name,
            feature_type=feature_type,
            error_message="Mutation transport succeeded, but the resulting feature could not be reread.",
            transport_ok=True,
            http_ok=True,
            regen_ok=(
                True
                if _regen_ok(status)
                else False
                if status != "UNKNOWN"
                else None
            ),
            mutation_verification="unverified",
            changed=None,
            verification_scope="feature_state",
            reason_code="AUTHORITATIVE_REREAD_FAILED",
            verification_message="Requested state could not be verified by authoritative reread.",
            raw={"response": response if isinstance(response, dict) else {}},
        )

    if not real_feature_id:
        return FeatureApplyResult(
            ok=False,
            status=status,
            feature_id="",
            feature_name=feature_name,
            feature_type=feature_type,
            error_message=(
                "Mutation transport succeeded, but Onshape did not return an "
                "identifier that can be correlated with the authoritative reread."
            ),
            transport_ok=True,
            http_ok=True,
            regen_ok=None,
            mutation_verification="unverified",
            changed=None,
            verification_scope="feature_state",
            reason_code="FEATURE_ID_UNAVAILABLE",
            verification_message=(
                "Requested state cannot be verified without a stable feature identifier."
            ),
            raw={"response": response, "reread": features_after},
        )

    actual_feature = _find_feature(features_after, real_feature_id)
    authoritative_state = _feature_state(features_after, real_feature_id)
    if authoritative_state:
        state = authoritative_state
        status = _normalize_status(authoritative_state.get("featureStatus"))
    else:
        state = {}
        status = "UNKNOWN"

    if actual_feature:
        feature_name = actual_feature.get("name") or feature_name
        feature_type = (
            actual_feature.get("featureType")
            or actual_feature.get("btType")
            or feature_type
        )

    if status != "UNKNOWN" and not _regen_ok(status):
        fs_status = await _fetch_feature_status_enum(
            client, document_id, workspace_id, element_id, real_feature_id
        )
        return FeatureApplyResult(
            ok=False,
            status=status,
            feature_id=real_feature_id,
            feature_name=feature_name,
            feature_type=feature_type,
            error_message=_extract_error_message(state or {}, fs_status=fs_status),
            transport_ok=True,
            http_ok=True,
            regen_ok=False,
            mutation_verification="failed",
            changed=False,
            verification_scope="feature_state",
            reason_code="FEATURE_REGENERATION_ERROR",
            verification_message="Authoritative reread reported a non-success regeneration status.",
            raw={"response": response, "reread": features_after},
        )

    if actual_feature is None:
        return FeatureApplyResult(
            ok=False,
            status=status,
            feature_id=real_feature_id,
            feature_name=feature_name,
            feature_type=feature_type,
            error_message="Mutation was accepted, but the target feature is absent on reread.",
            transport_ok=True,
            http_ok=True,
            regen_ok=True if _regen_ok(status) else None,
            mutation_verification="no_effect",
            changed=False,
            verification_scope="feature_state",
            reason_code="FEATURE_NOT_PRESENT_AFTER_MUTATION",
            verification_message="The expected feature was not present after mutation.",
            raw={"response": response, "reread": features_after},
        )

    if status == "UNKNOWN":
        return FeatureApplyResult(
            ok=False,
            status="UNKNOWN",
            feature_id=real_feature_id,
            feature_name=feature_name,
            feature_type=feature_type,
            error_message=(
                "The feature is present, but its authoritative regeneration "
                "status was unavailable."
            ),
            transport_ok=True,
            http_ok=True,
            regen_ok=None,
            mutation_verification="unverified",
            changed=None,
            verification_scope="feature_state",
            reason_code="FEATURE_STATUS_UNAVAILABLE",
            verification_message=(
                "Feature presence was confirmed, but regeneration success was not."
            ),
            raw={"response": response, "reread": features_after},
        )

    requested_feature = feature_payload.get("feature", feature_payload)
    comparison: Optional[bool] = None
    has_comparable_request = isinstance(requested_feature, dict) and any(
        key not in {"featureId", "nodeId"} for key in requested_feature
    )
    if verify_requested_state and has_comparable_request:
        comparison = _requested_subset_compare(requested_feature, actual_feature)

    if comparison is True:
        verification: MutationVerification = "verified"
        reason_code = "REQUESTED_STATE_VERIFIED"
        verification_message = "Requested feature fields match the authoritative reread."
        changed: Optional[bool] = True
    elif comparison is False:
        verification = "failed"
        reason_code = "REQUESTED_STATE_MISMATCH"
        verification_message = "Safely comparable requested feature fields do not match."
        changed = True
    else:
        verification = "unverified"
        reason_code = "REQUESTED_STATE_UNVERIFIED"
        verification_message = (
            "Feature presence and regeneration were confirmed, but requested fields "
            "could not be compared defensibly."
        )
        changed = None

    ok = verification == "verified"
    error_message = None if ok else verification_message

    # After-snapshot + diff. Only if caller asked AND before-snapshot succeeded
    # AND the feature actually built (diffing after an ERROR would likely just
    # show the pre-feature state unchanged).
    changes: Optional[Dict[str, Any]] = None
    if track_changes and bodies_before is not None and ok:
        try:
            bd_after = await client.get(
                f"/api/v9/partstudios/d/{document_id}/w/{workspace_id}/e/{element_id}/bodydetails"
            )
            bodies_after = bd_after.get("bodies") or []
            mass_after = await client.get(
                f"/api/v9/partstudios/d/{document_id}/w/{workspace_id}/e/{element_id}/massproperties"
            )
            from .geometry_diff import compute_diff
            changes = compute_diff(
                bodies_before, bodies_after,
                mass_before=mass_before, mass_after=mass_after,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "track_changes: diff failed ({}); skipping", safe_exception_message(e)
            )
            changes = None

    return FeatureApplyResult(
        ok=ok,
        status=status,
        feature_id=real_feature_id,
        feature_name=feature_name,
        feature_type=feature_type,
        error_message=error_message,
        changes=changes,
        transport_ok=True,
        http_ok=True,
        regen_ok=True,
        mutation_verification=verification,
        changed=changed,
        verification_scope="feature_state",
        reason_code=reason_code,
        verification_message=verification_message,
        raw={"response": response, "reread": features_after},
    )


async def apply_assembly_feature_and_check(
    client: OnshapeClient,
    document_id: str,
    workspace_id: str,
    element_id: str,
    feature_payload: Dict[str, Any],
    *,
    operation: Literal["create", "update"] = "create",
    feature_id: Optional[str] = None,
) -> FeatureApplyResult:
    """Apply a feature to an Assembly and return its Onshape-reported status.

    Assembly responses provide transport and regeneration evidence, but this
    helper has no authoritative requested-field reread. Mate connectors, mates
    (fastened / revolute / slider / cylindrical), and any other assembly
    feature therefore remain ``unverified`` after an OK/INFO response and
    surface ``failed`` when the solver reports a regeneration error.

    Response shape on the assembly side is identical to the PS side
    (`{featureState, feature, ...}`) — verified via live probe — so the
    same parsing works.
    """
    if operation == "update" and not feature_id:
        raise ValueError("feature_id is required when operation='update'")
    if operation not in {"create", "update"}:
        raise ValueError(f"operation must be 'create' or 'update', got {operation!r}")

    base = (
        f"/api/v9/assemblies/d/{document_id}/w/{workspace_id}/e/{element_id}/features"
    )
    path = base if operation == "create" else f"{base}/featureid/{feature_id}"

    response = await client.post(path, data=feature_payload)

    state = response.get("featureState") if isinstance(response, dict) else None
    feature = response.get("feature", {}) if isinstance(response, dict) else {}

    real_feature_id = feature.get("featureId") or feature_id or ""
    feature_name = feature.get("name", "")
    feature_type = feature.get("featureType") or feature.get("btType", "")

    if not state:
        # Fallback: re-read /features and pick this feature's state out of the
        # map. Onshape has been reliable about including `featureState` inline
        # on assembly POSTs, but the belt-and-suspenders path matches the PS
        # helper and is cheap.
        logger.warning(
            "apply_assembly_feature_and_check: POST response missing featureState; "
            "falling back to /features featureStates map"
        )
        try:
            feats = await client.get(base)
            state = (feats.get("featureStates") or {}).get(real_feature_id)
        except Exception as e:  # noqa: BLE001
            logger.error("Fallback /features GET failed: {}", safe_exception_message(e))
            state = None

    status = _normalize_status((state or {}).get("featureStatus", "UNKNOWN"))
    regen_ok: Optional[bool] = (
        True
        if _regen_ok(status)
        else False
        if status != "UNKNOWN"
        else None
    )

    error_message: Optional[str] = None
    if status != "OK":
        # Assembly contexts don't expose getFeatureStatus via FS (there's no
        # Part Studio context for eval), so this returns None and we fall
        # through to the legacy blob dump -- keeps the surface consistent.
        fs_status = await _fetch_feature_status_enum(
            client, document_id, workspace_id, element_id, real_feature_id,
            is_assembly=True,
        )
        error_message = _extract_error_message(state or {}, fs_status=fs_status)

    if regen_ok is False:
        verification: MutationVerification = "failed"
        reason_code = "FEATURE_REGENERATION_ERROR"
        verification_message = (
            "Onshape reported a non-success assembly regeneration status."
        )
    else:
        verification = "unverified"
        reason_code = (
            "REQUESTED_STATE_UNVERIFIED"
            if regen_ok is True
            else "FEATURE_STATUS_UNAVAILABLE"
        )
        verification_message = (
            "Assembly regeneration was accepted, but requested fields were not "
            "verified by an authoritative reread."
            if regen_ok is True
            else "Assembly regeneration status and requested state could not be verified."
        )
        error_message = verification_message

    return FeatureApplyResult(
        ok=False,
        status=status,
        feature_id=real_feature_id,
        feature_name=feature_name,
        feature_type=feature_type,
        error_message=error_message,
        transport_ok=True,
        http_ok=True,
        regen_ok=regen_ok,
        mutation_verification=verification,
        changed=None,
        verification_scope="response_feature_state",
        reason_code=reason_code,
        verification_message=verification_message,
        raw={"response": response if isinstance(response, dict) else {}},
    )


async def update_feature_params_and_check(
    client: OnshapeClient,
    document_id: str,
    workspace_id: str,
    element_id: str,
    feature_id: str,
    updates: List[Dict[str, Any]],
) -> FeatureApplyResult:
    """Patch a specific feature's parameters and report the real Onshape status.

    Onshape does not have a granular parameter-patch endpoint; updates are done
    by re-POSTing the whole feature to
    `/api/v9/partstudios/.../features/featureid/{feature_id}`. This helper hides
    that round-trip: it GETs the current /features list, finds the feature by
    id, merges the caller's `updates` into the matching parameters by
    `parameterId`, and POSTs the modified feature through
    `apply_feature_and_check` so the same structured status comes out.

    Args:
        client: Active OnshapeClient.
        document_id, workspace_id, element_id: Usual triple.
        feature_id: Feature to patch.
        updates: List of parameter patches. Each entry MUST include
            `parameterId`. Any other keys are merged into the matching
            parameter dict, overwriting. For BTMParameterQuantity-147 set
            `expression` (e.g. `"15 mm"`, `"90 deg"`) and the helper clears the
            stale numeric `value` so Onshape re-evaluates. For booleans / enums
            (BTMParameterBoolean-144 / BTMParameterEnum-145) just set `value`.

    Returns:
        FeatureApplyResult with the post-update featureStatus. ok=False if the
        feature errors after the patch (so Claude learns the tweak was wrong).

    Raises:
        ValueError: feature_id not found, or an `updates` entry has no
            matching parameterId, or `updates` is empty — all of these are
            programmer/driver errors, not API failures.
    """
    if not feature_id:
        raise MutationPreflightError("feature_id is required")
    if not updates:
        raise MutationPreflightError("updates must be a non-empty list")

    base = (
        f"/api/v9/partstudios/d/{document_id}/w/{workspace_id}/e/{element_id}/features"
    )
    features_doc = await client.get(base)
    features: List[Dict[str, Any]] = features_doc.get("features", []) or []

    target: Optional[Dict[str, Any]] = None
    for feat in features:
        if feat.get("featureId") == feature_id:
            target = feat
            break
    if target is None:
        raise MutationPreflightError(
            f"feature_id {feature_id!r} not found in element. "
            f"Available ids: {[f.get('featureId') for f in features]}"
        )

    before_target = copy.deepcopy(target)
    target = copy.deepcopy(target)
    params = target.get("parameters") or []
    param_by_id: Dict[str, Dict[str, Any]] = {
        p.get("parameterId"): p for p in params if isinstance(p, dict)
    }

    missing: List[str] = []
    for upd in updates:
        if not isinstance(upd, dict) or "parameterId" not in upd:
            raise MutationPreflightError(
                f"each update must be a dict with a 'parameterId' key, got {upd!r}"
            )
        pid = upd["parameterId"]
        target_param = param_by_id.get(pid)
        if target_param is None:
            missing.append(pid)
            continue
        # Merge all other fields into the parameter dict.
        for k, v in upd.items():
            if k == "parameterId":
                continue
            target_param[k] = v
        # For Quantity params: if caller set expression but didn't set value,
        # clear the numeric value so Onshape re-evaluates the expression
        # instead of preferring the stale numeric.
        if (
            target_param.get("btType") == "BTMParameterQuantity-147"
            and "expression" in upd
            and "value" not in upd
        ):
            target_param["value"] = 0.0

    if missing:
        existing = sorted(param_by_id.keys())
        raise MutationPreflightError(
            f"parameterId(s) not found on feature: {missing!r}. "
            f"Feature has parameters: {existing}"
        )

    before_params = {
        p.get("parameterId"): p
        for p in (before_target.get("parameters") or [])
        if isinstance(p, dict) and p.get("parameterId")
    }
    if all(
        _requested_update_compare(update, before_params[update["parameterId"]]) is True
        for update in updates
    ):
        state = _feature_state(features_doc, feature_id)
        status = _normalize_status(state.get("featureStatus", "UNKNOWN"))
        return FeatureApplyResult(
            ok=False,
            status=status,
            feature_id=feature_id,
            feature_name=before_target.get("name", ""),
            feature_type=(
                before_target.get("featureType") or before_target.get("btType", "")
            ),
            error_message="Requested parameter values were already present; no POST was sent.",
            transport_ok=None,
            http_ok=None,
            regen_ok=(
                True
                if _regen_ok(status)
                else False
                if status != "UNKNOWN"
                else None
            ),
            mutation_verification="no_effect",
            changed=False,
            verification_scope="requested_parameters",
            reason_code="REQUESTED_STATE_ALREADY_PRESENT",
            verification_message="The requested parameter state was already present.",
            raw={"reread": features_doc},
        )

    result = await apply_feature_and_check(
        client,
        document_id,
        workspace_id,
        element_id,
        {"feature": target},
        operation="update",
        feature_id=feature_id,
        verify_requested_state=False,
    )

    if result.regen_ok is not True:
        return result
    features_after = result.raw.get("reread")
    if not isinstance(features_after, dict):
        return result
    after_target = _find_feature(features_after, feature_id)
    if after_target is None:
        return result
    after_params = {
        p.get("parameterId"): p
        for p in (after_target.get("parameters") or [])
        if isinstance(p, dict) and p.get("parameterId")
    }

    comparisons: List[Optional[bool]] = []
    for update in updates:
        after_param = after_params.get(update["parameterId"])
        comparisons.append(
            None
            if after_param is None
            else _requested_update_compare(update, after_param)
        )

    if all(value is True for value in comparisons):
        result.ok = True
        result.mutation_verification = "verified"
        result.changed = True
        result.verification_scope = "requested_parameters"
        result.reason_code = "REQUESTED_STATE_VERIFIED"
        result.verification_message = (
            "All requested parameter fields match the authoritative reread."
        )
        result.error_message = None
        return result

    unchanged = True
    for update in updates:
        before_param = before_params[update["parameterId"]]
        after_param = after_params.get(update["parameterId"])
        if after_param is None:
            unchanged = False
            break
        for key in update:
            if key == "parameterId":
                continue
            if key not in before_param or key not in after_param:
                unchanged = False
                break
            if not _scalar_equal(before_param[key], after_param[key]):
                unchanged = False
                break
        if not unchanged:
            break

    result.ok = False
    result.verification_scope = "requested_parameters"
    if unchanged:
        result.mutation_verification = "no_effect"
        result.changed = False
        result.reason_code = "REQUESTED_STATE_UNCHANGED"
        result.verification_message = (
            "Transport and regeneration succeeded, but requested parameters were unchanged."
        )
    elif any(value is False for value in comparisons):
        result.mutation_verification = "failed"
        result.changed = True
        result.reason_code = "REQUESTED_STATE_MISMATCH"
        result.verification_message = (
            "Safely comparable requested parameter fields do not match the reread."
        )
    else:
        result.mutation_verification = "unverified"
        result.changed = None
        result.reason_code = "REQUESTED_STATE_UNVERIFIED"
        result.verification_message = (
            "Post-write parameters could not be compared defensibly with every requested field."
        )
    result.error_message = result.verification_message
    return result


async def delete_partstudio_feature_and_check(
    manager: Any,
    document_id: str,
    workspace_id: str,
    element_id: str,
    feature_id: str,
    *,
    features_before: Optional[Dict[str, Any]] = None,
) -> FeatureApplyResult:
    """Delete a Part Studio feature and verify absence on authoritative reread."""

    if features_before is None:
        features_before = await manager.get_features(
            document_id, workspace_id, element_id
        )
    target = _find_feature(features_before, feature_id)
    if target is None:
        return FeatureApplyResult(
            ok=False,
            status="UNKNOWN",
            feature_id=feature_id,
            feature_name="",
            feature_type="",
            error_message="Feature was absent before deletion; no request was sent.",
            transport_ok=None,
            http_ok=None,
            regen_ok=None,
            mutation_verification="no_effect",
            changed=False,
            verification_scope="feature_absence",
            reason_code="FEATURE_ALREADY_ABSENT",
        )

    before_error_ids = {
        item_id
        for item_id, state in (features_before.get("featureStates") or {}).items()
        if isinstance(state, dict) and state.get("featureStatus") == "ERROR"
    }
    await manager.delete_feature(document_id, workspace_id, element_id, feature_id)
    try:
        features_after = await manager.get_features(
            document_id, workspace_id, element_id
        )
    except Exception as error:  # noqa: BLE001
        logger.warning(
            "delete_partstudio_feature_and_check: authoritative reread failed ({})",
            type(error).__name__,
        )
        return FeatureApplyResult(
            ok=False,
            status="UNKNOWN",
            feature_id=feature_id,
            feature_name=target.get("name", ""),
            feature_type=target.get("featureType") or target.get("btType", ""),
            error_message="Deletion transport succeeded, but absence could not be verified.",
            transport_ok=True,
            http_ok=True,
            regen_ok=None,
            mutation_verification="unverified",
            changed=None,
            verification_scope="feature_absence",
            reason_code="AUTHORITATIVE_REREAD_FAILED",
        )

    if _find_feature(features_after, feature_id) is not None:
        retained_status = _normalize_status(
            _feature_state(features_after, feature_id).get("featureStatus")
        )
        return FeatureApplyResult(
            ok=False,
            status=retained_status,
            feature_id=feature_id,
            feature_name=target.get("name", ""),
            feature_type=target.get("featureType") or target.get("btType", ""),
            error_message="Delete request was accepted, but the feature remains present.",
            transport_ok=True,
            http_ok=True,
            regen_ok=(
                True
                if _regen_ok(retained_status)
                else False
                if retained_status != "UNKNOWN"
                else None
            ),
            mutation_verification="no_effect",
            changed=False,
            verification_scope="feature_absence",
            reason_code="FEATURE_RETAINED_AFTER_DELETE",
            raw={"reread": features_after},
        )

    states_after = features_after.get("featureStates")
    if not isinstance(states_after, dict):
        return FeatureApplyResult(
            ok=False,
            status="UNKNOWN",
            feature_id=feature_id,
            feature_name=target.get("name", ""),
            feature_type=target.get("featureType") or target.get("btType", ""),
            error_message=(
                "Feature absence was confirmed, but post-delete regeneration "
                "status was unavailable."
            ),
            transport_ok=True,
            http_ok=True,
            regen_ok=None,
            mutation_verification="unverified",
            changed=True,
            verification_scope="feature_absence",
            reason_code="REGENERATION_STATUS_UNAVAILABLE_AFTER_DELETE",
            verification_message=(
                "Deletion changed the feature list, but regeneration health is unverified."
            ),
            raw={"reread": features_after},
        )

    after_error_ids = {
        item_id
        for item_id, state in states_after.items()
        if isinstance(state, dict) and state.get("featureStatus") == "ERROR"
    }
    if after_error_ids - before_error_ids:
        return FeatureApplyResult(
            ok=False,
            status="ERROR",
            feature_id=feature_id,
            feature_name=target.get("name", ""),
            feature_type=target.get("featureType") or target.get("btType", ""),
            error_message="Feature was removed, but the reread contains new regeneration errors.",
            transport_ok=True,
            http_ok=True,
            regen_ok=False,
            mutation_verification="failed",
            changed=True,
            verification_scope="feature_absence",
            reason_code="DELETE_TRIGGERED_REGENERATION_ERROR",
            raw={"reread": features_after},
        )

    return FeatureApplyResult(
        ok=True,
        status="OK",
        feature_id=feature_id,
        feature_name=target.get("name", ""),
        feature_type=target.get("featureType") or target.get("btType", ""),
        transport_ok=True,
        http_ok=True,
        regen_ok=True,
        mutation_verification="verified",
        changed=True,
        verification_scope="feature_absence",
        reason_code="FEATURE_ABSENCE_VERIFIED",
        verification_message="Feature absence was confirmed by authoritative reread.",
        raw={"reread": features_after},
    )


def _extract_error_message(
    state: Dict[str, Any],
    fs_status: Optional[Dict[str, Any]] = None,
) -> str:
    """Pull a useful error string out of a BTFeatureState blob.

    Onshape's `featureState` wire field only carries `{btType, featureStatus,
    inactive}` on most sketch/extrude warnings -- no `message`, no `feedback`.
    The diagnostic (`SKETCH_DIMENSION_MISSING_PARAMETER`, etc.) lives inside
    the FS runtime and is only reachable by calling `getFeatureStatus(context,
    id)` via `/featurescript`. `fs_status` is the unwrapped result of that
    call, carrying `{statusEnum?, statusType}`. We prefer it over the blob
    because the enum is a machine-readable, greppable handle callers can act
    on.

    Fall-through order:
      1. `fs_status.statusEnum` + `statusType` (new, always actionable)
      2. `state.message` (rarely populated in practice)
      3. `state.feedback[].{severity, message}` (rarely populated)
      4. Bounded status-only fallback (never a raw state dump)
    """

    parts: List[str] = []

    if isinstance(fs_status, dict):
        enum_val = fs_status.get("statusEnum")
        type_val = fs_status.get("statusType")
        if enum_val:
            # Machine-readable. Keep it prominent but include a human hint.
            parts.append(
                f"{enum_val} ({type_val})" if type_val else str(enum_val)
            )

    message = state.get("message")
    feedback = state.get("feedback")

    if isinstance(message, str) and message.strip():
        parts.append(message.strip())

    if isinstance(feedback, list):
        for item in feedback:
            if not isinstance(item, dict):
                continue
            sev = item.get("severity") or item.get("level") or ""
            msg = item.get("message") or item.get("text") or ""
            if msg:
                parts.append(f"[{sev}] {msg}" if sev else str(msg))

    if parts:
        return " | ".join(parts)

    status = state.get("featureStatus") or "UNKNOWN"
    return f"Onshape reported featureStatus={status} without a structured message."


async def _fetch_feature_status_enum(
    client: OnshapeClient,
    document_id: str,
    workspace_id: str,
    element_id: str,
    feature_id: str,
    *,
    is_assembly: bool = False,
) -> Optional[Dict[str, Any]]:
    """Call FS `getFeatureStatus(context, id)` for a specific feature and
    return the unwrapped `{statusEnum, statusType}` map (or None on failure).

    Only runs on non-OK statuses; the happy path never pays for this. On any
    error (bad response shape, network blip, assembly context that can't run
    FS) returns None so the caller falls back to the blob dump -- enrichment
    is best-effort, it must never fail the write.
    """
    if not feature_id:
        return None
    kind = "assemblies" if is_assembly else "partstudios"
    path = f"/api/v8/{kind}/d/{document_id}/w/{workspace_id}/e/{element_id}/featurescript"
    # Escape double quotes / backslashes in the id for safety, though real
    # Onshape featureIds never contain those.
    safe_id = feature_id.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        "function(context is Context, queries) {\n"
        f'    return getFeatureStatus(context, ["{safe_id}"] as Id);\n'
        "}"
    )
    try:
        resp = await client.post(path, data={"script": script})
    except Exception as e:  # noqa: BLE001
        logger.debug("getFeatureStatus FS call failed: {}", safe_exception_message(e))
        return None

    return _unwrap_fsvalue(resp.get("result"))


def _unwrap_fsvalue(v: Any) -> Any:
    """Convert a BTFSValue* tree back to plain Python.

    FS returns all values wrapped in `{btType: "...BTFSValue<kind>", value: ...}`.
    Maps nest further as lists of `{key, value}` entries. Arrays are lists of
    wrapped values. Scalars (string/bool/number/undefined) carry the value
    directly on the wrapper. This is a narrow-scope helper for the enrichment
    path; the full rendering module has its own unwrapper.
    """
    if not isinstance(v, dict):
        return v
    btt = v.get("btType", "")
    if "ValueMap" in btt:
        out: Dict[Any, Any] = {}
        for ent in v.get("value") or []:
            if not isinstance(ent, dict):
                continue
            k = _unwrap_fsvalue(ent.get("key"))
            out[k] = _unwrap_fsvalue(ent.get("value"))
        return out
    if "ValueArray" in btt:
        return [_unwrap_fsvalue(x) for x in (v.get("value") or [])]
    if "ValueUndefined" in btt:
        return None
    # Scalars (string, boolean, number, value-with-units) expose the payload
    # directly under `value`.
    return v.get("value")
