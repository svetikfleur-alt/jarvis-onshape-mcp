import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from onshape_mcp.api.drawing_ocr import (
    Callout,
    _classify,
    callouts_to_dict,
    extract_callouts,
)


def _install_fake_ocr(monkeypatch: pytest.MonkeyPatch, data: dict[str, list[object]]) -> None:
    fake_pytesseract = SimpleNamespace(
        Output=SimpleNamespace(DICT="DICT"),
        image_to_data=lambda *args, **kwargs: data,
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake_pytesseract)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ø12", "diameter"),
        ("R3.5", "radius"),
        ("4X", "count"),
        ("M8X1.25", "thread"),
        ("30.0°", "angle"),
        ("1:5", "scale"),
        ("(12.5),", "length"),
        ("datum-A", "other"),
    ],
)
def test_classify_recognizes_engineering_callout_shapes(text: str, expected: str) -> None:
    assert _classify(text) == expected


def test_extract_callouts_filters_groups_and_computes_bounds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "drawing.png"
    Image.new("RGB", (2, 2), "white").save(image_path)
    data = {
        "text": ["4X", "R12", "NOTE", "30°", "bad", "", "M8"],
        "conf": ["90", "70", "99", "80", "not-a-number", "99", "50"],
        "left": [0, 12, 100, 0, 0, 0, 200],
        "top": [0, 5, 0, 40, 80, 100, 40],
        "width": [10, 12, 20, 10, 10, 10, 10],
        "height": [5, 6, 5, 5, 5, 5, 5],
    }
    _install_fake_ocr(monkeypatch, data)

    result = extract_callouts(str(image_path), min_confidence=30)

    assert result == [
        Callout(
            text="4X R12",
            x=0,
            y=0,
            width=24,
            height=11,
            confidence=80.0,
            kind="count",
        ),
        Callout(
            text="30°",
            x=0,
            y=40,
            width=10,
            height=5,
            confidence=80.0,
            kind="angle",
        ),
        Callout(
            text="M8",
            x=200,
            y=40,
            width=10,
            height=5,
            confidence=50.0,
            kind="thread",
        ),
    ]


def test_callouts_to_dict_groups_supported_dimension_kinds() -> None:
    callouts = [
        Callout(
            text=text,
            x=index,
            y=0,
            width=1,
            height=1,
            confidence=90.0,
            kind=kind,
        )
        for index, (kind, text) in enumerate(
            [
                ("length", "10"),
                ("radius", "R2"),
                ("diameter", "Ø4"),
                ("thread", "M8"),
                ("angle", "30°"),
                ("count", "4X"),
                ("scale", "1:2"),
                ("other", "A1"),
            ]
        )
    ]

    result = callouts_to_dict(callouts)

    assert result["callouts"][0] == {
        "text": "10",
        "x": 0,
        "y": 0,
        "width": 1,
        "height": 1,
        "confidence": 90.0,
        "kind": "length",
    }
    assert result["by_kind"] == {
        "length": ["10"],
        "radius": ["R2"],
        "diameter": ["Ø4"],
        "thread": ["M8"],
        "angle": ["30°"],
        "count": ["4X"],
        "scale": ["1:2"],
    }
