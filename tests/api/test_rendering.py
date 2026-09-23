"""Offline behavior tests for render caching and image manipulation."""

import base64
import io

import pytest
from PIL import Image

from onshape_mcp.api import rendering
from onshape_mcp.api.rendering import (
    RenderedView,
    ShadedViewManager,
    compose_reference_comparison,
    crop_cached_image,
    get_image,
    get_image_meta,
    list_cached_image_ids,
    load_local_image,
)


def _png(width=8, height=6, color=(10, 20, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _isolated_image_cache(monkeypatch):
    monkeypatch.setattr(rendering, "_IMAGE_CACHE", {})
    monkeypatch.setattr(rendering, "_IMAGE_META", {})
    monkeypatch.delenv("ONSHAPE_MCP_MIRROR_IMAGES_DIR", raising=False)


class _Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def get(self, path, params=None):
        self.calls.append((path, params))
        return next(self.responses)


def test_cache_deduplicates_content_and_exposes_current_metadata():
    content = _png()

    first = rendering._put_image(content, {"view": "front"})
    second = rendering._put_image(content, {"view": "updated"})

    assert first == second
    assert get_image(first) == content
    assert get_image_meta(first) == {"view": "updated"}
    assert list_cached_image_ids() == [
        {"image_id": first, "view": "updated", "bytes": len(content)}
    ]
    assert RenderedView("front", first, 8, 6, len(content)).to_dict() == {
        "view": "front",
        "image_id": first,
        "width": 8,
        "height": 6,
        "bytes": len(content),
    }


def test_cache_mirroring_is_best_effort_and_never_overwrites(tmp_path, monkeypatch):
    mirror = tmp_path / "mirror"
    monkeypatch.setenv("ONSHAPE_MCP_MIRROR_IMAGES_DIR", str(mirror))
    content = _png()

    image_id = rendering._put_image(content, {})
    mirrored = mirror / f"{image_id}.png"
    mirrored.write_bytes(b"observer annotation")
    rendering._put_image(content, {"again": True})

    assert mirrored.read_bytes() == b"observer annotation"

    blocker = tmp_path / "regular-file"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("ONSHAPE_MCP_MIRROR_IMAGES_DIR", str(blocker / "child"))
    assert rendering._put_image(b"still cached", {}) == (
        "img_" + rendering.hashlib.sha256(b"still cached").hexdigest()[:16]
    )


@pytest.mark.asyncio
async def test_part_studio_render_encodes_named_and_raw_views_with_parameters():
    first = _png(color=(255, 0, 0))
    second = _png(color=(0, 255, 0))
    client = _Client(
        [
            {"images": [base64.b64encode(first).decode()]},
            {"images": [base64.b64encode(second).decode()]},
        ]
    )

    views = await ShadedViewManager(client).render_part_studio_views(
        "D",
        "W",
        "E",
        views=["ISO", "1,0,0,0,1,0,0,0,1,0,0,0"],
        width=640,
        height=480,
        pixel_size=0.25,
        edges=False,
    )

    assert [view.view for view in views] == ["ISO", "1,0,0,0,1,0,0,0,1,0,0,0"]
    assert all(view.width == 640 and view.height == 480 for view in views)
    assert client.calls[0] == (
        "/api/v9/partstudios/d/D/w/W/e/E/shadedviews",
        {
            "viewMatrix": "isometric",
            "outputWidth": 640,
            "outputHeight": 480,
            "pixelSize": 0.25,
            "edges": "false",
        },
    )
    assert client.calls[1][1]["viewMatrix"] == "1,0,0,0,1,0,0,0,1,0,0,0"
    assert get_image_meta(views[0].image_id)["source"] == {
        "kind": "partstudio",
        "did": "D",
        "wid": "W",
        "eid": "E",
    }


@pytest.mark.asyncio
async def test_assembly_default_views_use_assembly_endpoint_and_edges():
    response = {"images": [base64.b64encode(_png()).decode()]}
    client = _Client([response, response, response, response])

    views = await ShadedViewManager(client).render_assembly_views("D", "W", "A")

    assert [view.view for view in views] == ["iso", "top", "front", "right"]
    assert all(call[0] == "/api/v9/assemblies/d/D/w/W/e/A/shadedviews" for call in client.calls)
    assert [call[1]["viewMatrix"] for call in client.calls] == [
        "isometric",
        "top",
        "front",
        "right",
    ]
    assert all(call[1]["edges"] == "true" for call in client.calls)


@pytest.mark.asyncio
async def test_render_fails_with_response_shape_without_leaking_body():
    client = _Client([{"error": "PRIVATE RAW BODY"}])

    with pytest.raises(RuntimeError) as error:
        await ShadedViewManager(client).render_part_studio_views(
            "D", "W", "E", views=["front"]
        )

    assert "returned no images" in str(error.value)
    assert "PRIVATE RAW BODY" not in str(error.value)
    assert "error" in str(error.value)


def test_load_local_image_resizes_long_edge_and_caches_png(tmp_path):
    path = tmp_path / "brief.png"
    Image.new("RGB", (200, 100), "navy").save(path)

    view = load_local_image(str(path), max_edge=50)

    assert view.view == "local:brief.png"
    assert (view.width, view.height) == (50, 25)
    assert Image.open(io.BytesIO(get_image(view.image_id))).size == (50, 25)
    assert get_image_meta(view.image_id)["source"]["kind"] == "local"


def test_load_local_image_preserves_small_dimensions_and_rejects_missing_path(tmp_path):
    path = tmp_path / "small.png"
    Image.new("RGB", (20, 10), "white").save(path)

    view = load_local_image(str(path), max_edge=50)

    assert (view.width, view.height) == (20, 10)
    with pytest.raises(FileNotFoundError, match="load_local_image"):
        load_local_image(str(tmp_path / "missing.png"))


def test_comparison_composes_different_height_views_and_records_world_bbox(tmp_path):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (120, 60), "gray").save(reference)
    first_id = rendering._put_image(_png(40, 20, (255, 0, 0)), {})
    second_id = rendering._put_image(_png(20, 10, (0, 255, 0)), {})
    views = [
        RenderedView("front", first_id, 40, 20, len(get_image(first_id))),
        RenderedView("right", second_id, 20, 10, len(get_image(second_id))),
    ]

    comparison = compose_reference_comparison(
        str(reference), views, agent_bbox_mm=(100.0, 50.0, 25.0)
    )

    assert comparison.view == "reference_vs_build"
    assert comparison.width == 80
    assert comparison.height == 140
    assert get_image_meta(comparison.image_id)["agent_views"] == ["front", "right"]
    assert get_image_meta(comparison.image_id)["agent_image_ids"] == [first_id, second_id]


def test_comparison_caps_oversized_output_and_validates_inputs(tmp_path):
    missing = tmp_path / "missing.png"
    with pytest.raises(FileNotFoundError, match="reference image not found"):
        compose_reference_comparison(str(missing), [])

    reference = tmp_path / "reference.png"
    Image.new("RGB", (100, 100), "white").save(reference)
    with pytest.raises(ValueError, match="at least 1 rendered view"):
        compose_reference_comparison(str(reference), [])

    large_id = rendering._put_image(_png(1800, 100, (0, 0, 0)), {})
    result = compose_reference_comparison(
        str(reference),
        [RenderedView("wide", large_id, 1800, 100, len(get_image(large_id)))],
    )
    assert max(result.width, result.height) == 1568


def test_crop_clamps_bounds_preserves_metadata_and_rejects_empty_box():
    source_id = rendering._put_image(_png(100, 80), {"view": "front"})

    cropped = crop_cached_image(source_id, -1, 0.25, 0.5, 2)

    assert (cropped.width, cropped.height) == (50, 60)
    assert get_image_meta(cropped.image_id) == {
        "view": "front",
        "crop_of": source_id,
        "crop_bbox": [0.0, 0.25, 0.5, 1.0],
        "crop_px": [0, 20, 50, 80],
        "width": 50,
        "height": 60,
    }
    with pytest.raises(ValueError, match="invalid crop bbox"):
        crop_cached_image(source_id, 0.8, 0.2, 0.2, 0.9)


def test_unknown_cache_id_raises_key_error_and_meta_defaults_empty():
    assert get_image_meta("absent") == {}
    with pytest.raises(KeyError):
        get_image("absent")
