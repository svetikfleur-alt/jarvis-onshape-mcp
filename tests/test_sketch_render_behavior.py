import io

import pytest
from PIL import Image, ImageDraw

from onshape_mcp.api import sketch_render


def _open_png(png: bytes) -> Image.Image:
    with Image.open(io.BytesIO(png)) as image:
        image.load()
        return image.copy()


def test_quarter_arc_uses_the_short_sweep_between_its_endpoints() -> None:
    image = Image.new("RGB", (101, 101), "white")
    transform = sketch_render._Transform(
        scale=4.0,
        x_min=-10.0,
        y_min=-10.0,
        x_max=10.0,
        y_max=10.0,
        pad=10,
        height=101,
    )
    sketch_render._draw_arc(
        ImageDraw.Draw(image),
        transform,
        {
            "center_mm": (0.0, 0.0),
            "radius_mm": 5.0,
            "start_mm": (5.0, 0.0),
            "end_mm": (0.0, 5.0),
            "sweep_deg": 90.0,
        },
        (255, 0, 0),
    )

    assert image.getpixel((64, 37)) == (255, 0, 0)
    assert image.getpixel((36, 65)) == (255, 255, 255)


def test_reflex_arc_uses_the_long_sweep_between_its_endpoints() -> None:
    image = Image.new("RGB", (101, 101), "white")
    transform = sketch_render._Transform(
        scale=4.0,
        x_min=-10.0,
        y_min=-10.0,
        x_max=10.0,
        y_max=10.0,
        pad=10,
        height=101,
    )
    sketch_render._draw_arc(
        ImageDraw.Draw(image),
        transform,
        {
            "center_mm": (0.0, 0.0),
            "radius_mm": 5.0,
            "start_mm": (5.0, 0.0),
            "end_mm": (0.0, 5.0),
            "sweep_deg": 270.0,
        },
        (255, 0, 0),
    )

    assert image.getpixel((36, 65)) == (255, 0, 0)
    assert image.getpixel((64, 37)) == (255, 255, 255)


def test_render_draws_supported_geometry_and_constraint_badges() -> None:
    entities = [
        {
            "id": "line",
            "kind": "line",
            "start_mm": (-20.0, 0.0),
            "end_mm": (20.0, 0.0),
            "isConstruction": False,
        },
        {
            "id": "construction",
            "kind": "line",
            "start_mm": (0.0, -20.0),
            "end_mm": (0.0, 20.0),
            "isConstruction": True,
        },
        {
            "id": "zero",
            "kind": "line",
            "start_mm": (5.0, 5.0),
            "end_mm": (5.0, 5.0),
            "isConstruction": True,
        },
        {
            "id": "arc",
            "kind": "arc",
            "center_mm": (0.0, 0.0),
            "radius_mm": 12.0,
            "start_mm": (12.0, 0.0),
            "end_mm": (0.0, 12.0),
            "sweep_deg": 90.0,
        },
        {
            "id": "circle",
            "kind": "circle",
            "center_mm": (-12.0, 10.0),
            "radius_mm": 4.0,
        },
        {"id": "point", "kind": "point", "point_mm": (12.0, -10.0)},
        {"id": "other", "kind": "other"},
        {
            "kind": "line",
            "start_mm": (-15.0, -15.0),
            "end_mm": (-10.0, -10.0),
        },
    ]
    constraints = [
        {"constraintType": "FIX", "localFirst": "point"},
        {"constraintType": "FIX", "localFirst": "missing"},
        {"constraintType": "HORIZONTAL", "localFirst": "line"},
        {"constraintType": "VERTICAL", "localFirst": "point"},
        {"constraintType": "VERTICAL", "localFirst": "missing"},
        {"constraintType": "LENGTH", "localFirst": "line", "length": "40 mm"},
        {"constraintType": "DIAMETER", "localFirst": "circle", "length": "8 mm"},
        {"constraintType": "RADIUS", "localFirst": "arc"},
        {"constraintType": "RADIUS", "localFirst": None, "length": "12 mm"},
        {"constraintType": "LENGTH", "localFirst": "other", "length": "1 mm"},
        {
            "constraintType": "DISTANCE",
            "localFirst": "line.start",
            "localSecond": "line.end",
            "length": "40 mm",
            "direction": "HORIZONTAL",
        },
        {
            "constraintType": "DISTANCE",
            "localFirst": "construction.start",
            "localSecond": "construction.end",
            "length": "40 mm",
            "direction": "MINIMUM",
        },
        {
            "constraintType": "DISTANCE",
            "localFirst": "missing",
            "localSecond": "line.end",
            "length": "1 mm",
        },
        {
            "constraintType": "DISTANCE",
            "localFirst": "line.start",
            "localSecond": "line.end",
        },
        {"constraintType": "ANGLE", "localFirst": "point", "angle": "45 deg"},
        {"constraintType": "ANGLE", "localFirst": "missing", "angle": "45 deg"},
        {"constraintType": "ANGLE", "localFirst": "point"},
        {"constraintType": "COINCIDENT", "localFirst": "line.start"},
    ]

    image = _open_png(
        sketch_render.render_sketch_png(
            entities,
            constraints,
            width=420,
            height=320,
            title="Constrained profile",
        )
    )

    assert image.size == (420, 320)
    assert image.mode == "RGB"
    palette = {color: count for count, color in image.getcolors(maxcolors=420 * 320) or []}
    for color in (
        sketch_render._LINE,
        sketch_render._CONSTRUCTION,
        sketch_render._ARC,
        sketch_render._POINT,
        sketch_render._FIX_MARK,
        sketch_render._DIM_LABEL,
    ):
        assert palette[color] > 0
    without_hv = _open_png(
        sketch_render.render_sketch_png(
            entities,
            [
                constraint
                for constraint in constraints
                if constraint["constraintType"] not in ("HORIZONTAL", "VERTICAL")
            ],
            width=420,
            height=320,
            title="Constrained profile",
        )
    )
    assert image.tobytes() != without_hv.tobytes()


def test_render_supports_empty_sketch_and_optional_annotation_flags() -> None:
    entities = [
        {
            "id": "line",
            "kind": "line",
            "start_mm": (0.0, 0.0),
            "end_mm": (10.0, 0.0),
        }
    ]
    constraints = [
        {"constraintType": "HORIZONTAL", "localFirst": "line"},
    ]

    annotated = sketch_render.render_sketch_png(
        entities,
        constraints,
        width=160,
        height=120,
        title="Line",
    )
    plain = sketch_render.render_sketch_png(
        entities,
        constraints,
        width=160,
        height=120,
        show_labels=False,
        show_constraints=False,
    )
    empty = _open_png(
        sketch_render.render_sketch_png(
            [],
            [],
            width=120,
            height=100,
            show_labels=False,
            show_constraints=False,
        )
    )

    assert annotated.startswith(b"\x89PNG\r\n\x1a\n")
    assert plain.startswith(b"\x89PNG\r\n\x1a\n")
    assert annotated != plain
    assert empty.size == (120, 100)


def test_reference_resolution_handles_entities_subpoints_and_invalid_refs() -> None:
    entities = {
        "line": {
            "kind": "line",
            "start_mm": (1.0, 2.0),
            "end_mm": (5.0, 6.0),
        },
        "arc": {"kind": "arc", "center_mm": (3.0, 4.0)},
        "circle": {"kind": "circle", "center_mm": (7.0, 8.0)},
        "point": {"kind": "point", "point_mm": (9.0, 10.0)},
        "other": {"kind": "other"},
    }

    assert sketch_render._resolve_ref_point("line", entities) == (3.0, 4.0)
    assert sketch_render._resolve_ref_point("arc", entities) == (3.0, 4.0)
    assert sketch_render._resolve_ref_point("circle", entities) == (7.0, 8.0)
    assert sketch_render._resolve_ref_point("point", entities) == (9.0, 10.0)
    assert sketch_render._resolve_ref_point("line.start", entities) == (1.0, 2.0)
    assert sketch_render._resolve_ref_point("line.end", entities) == (5.0, 6.0)
    assert sketch_render._resolve_ref_point("circle.center", entities) == (7.0, 8.0)
    for invalid in (None, 3, "missing", "other", "line.center", "point.start"):
        assert sketch_render._resolve_ref_point(invalid, entities) is None


@pytest.mark.parametrize(
    ("span", "divisions", "expected"),
    [
        (0.0, 10, 10.0),
        (10.0, 10, 1.0),
        (15.0, 10, 2.0),
        (40.0, 10, 5.0),
        (80.0, 10, 10.0),
        (2.0, 0, 2.0),
    ],
)
def test_grid_step_uses_readable_one_two_five_progression(
    span: float,
    divisions: int,
    expected: float,
) -> None:
    assert sketch_render._nice_grid_step(span, divisions) == expected


def test_transform_includes_origin_and_maps_positive_y_upward() -> None:
    transform = sketch_render._compute_transform(
        [{"kind": "point", "point_mm": (10.0, 20.0)}],
        width=200,
        height=160,
        pad=40,
    )
    origin_u, origin_v = transform.to_px(0.0, 0.0)
    point_u, point_v = transform.to_px(10.0, 20.0)

    assert transform.x_min < 0.0 < transform.x_max
    assert transform.y_min < 0.0 < transform.y_max
    assert point_u > origin_u
    assert point_v < origin_v


@pytest.mark.parametrize(
    ("width", "height", "invalid_name"),
    [
        (4097, 100, "width"),
        (100, 4097, "height"),
        (80, 100, "width"),
        (100, 80, "height"),
    ],
)
def test_render_rejects_invalid_dimensions_before_allocating_image(
    monkeypatch: pytest.MonkeyPatch,
    width: int,
    height: int,
    invalid_name: str,
) -> None:
    def fail_if_allocated(*args: object, **kwargs: object) -> None:
        raise AssertionError("image allocation must not run for an oversized render")

    monkeypatch.setattr(sketch_render.Image, "new", fail_if_allocated)

    with pytest.raises(ValueError, match=invalid_name):
        sketch_render.render_sketch_png([], [], width=width, height=height)
