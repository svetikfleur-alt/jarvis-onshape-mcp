"""Deterministic contracts for baseline-aware document hygiene."""

from onshape_mcp.governance.hygiene import DocumentHygieneTracker


def _element(element_id: str, name: str, element_type: str) -> dict[str, str]:
    return {"id": element_id, "name": name, "type": element_type}


def test_default_elements_captured_at_bootstrap_are_not_jarvis_clutter() -> None:
    tracker = DocumentHygieneTracker()
    baseline = [
        _element("ps-default", "Part Studio 1", "PARTSTUDIO"),
        _element("asm-default", "Assembly 1", "ASSEMBLY"),
        _element("bom-default", "BOM", "BOM"),
    ]
    tracker.capture_baseline("doc", "ws", baseline)
    tracker.register_addition(
        "doc",
        "ws",
        _element("ps-final", "Enclosure", "PARTSTUDIO"),
        classification="intentional",
    )

    result = tracker.evaluate(
        "doc", "ws", [*baseline, _element("ps-final", "Enclosure", "PARTSTUDIO")]
    )

    assert [item["id"] for item in result["baseline_elements"]] == [
        "asm-default",
        "bom-default",
        "ps-default",
    ]
    assert [item["id"] for item in result["intentional_additions"]] == [
        "ps-final"
    ]
    assert result["temporary_jarvis_additions"] == []
    assert result["remaining_unexpected_jarvis_artifacts"] == []
    assert result["status"] == "CLEAN"


def test_only_tracked_temporary_leftovers_make_document_not_clean() -> None:
    tracker = DocumentHygieneTracker()
    tracker.capture_baseline(
        "doc", "ws", [_element("asm-default", "Assembly 1", "ASSEMBLY")]
    )
    tracker.register_addition(
        "doc",
        "ws",
        _element("temp-fs", "Jarvis temporary probe", "FEATURESTUDIO"),
        classification="temporary",
    )
    current = [
        _element("asm-default", "Assembly 1", "ASSEMBLY"),
        _element("temp-fs", "Jarvis temporary probe", "FEATURESTUDIO"),
        _element("external", "User notes", "BLOB"),
    ]

    result = tracker.evaluate("doc", "ws", current)

    assert [item["id"] for item in result["temporary_jarvis_additions"]] == [
        "temp-fs"
    ]
    assert [
        item["id"] for item in result["remaining_unexpected_jarvis_artifacts"]
    ] == ["temp-fs"]
    assert [item["id"] for item in result["unknown_additions"]] == ["external"]
    assert result["status"] == "NOT CLEAN"


def test_evaluation_requires_a_bootstrap_baseline() -> None:
    tracker = DocumentHygieneTracker()

    result = tracker.evaluate(
        "unknown-doc", "unknown-ws", [_element("a", "Assembly 1", "ASSEMBLY")]
    )

    assert result == {
        "baseline_available": False,
        "status": "UNKNOWN",
        "reason": "No bootstrap baseline is available for this document workspace.",
    }
