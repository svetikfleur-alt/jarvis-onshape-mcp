"""Cross-builder regressions for current Onshape parameter serialization."""

from typing import Any

import pytest

from onshape_mcp.builders.boolean import BooleanBuilder
from onshape_mcp.builders.chamfer import ChamferBuilder
from onshape_mcp.builders.extrude import ExtrudeBuilder
from onshape_mcp.builders.fillet import FilletBuilder
from onshape_mcp.builders.offset_plane import OffsetPlaneBuilder
from onshape_mcp.builders.pattern import CircularPatternBuilder, LinearPatternBuilder
from onshape_mcp.builders.shell import ShellBuilder
from onshape_mcp.builders.sketch_constraints import serialize as serialize_constraint


def _contains_key(value: Any, forbidden: str) -> bool:
    if isinstance(value, dict):
        return forbidden in value or any(
            _contains_key(item, forbidden) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key(item, forbidden) for item in value)
    return False


def _affected_payloads() -> list[dict]:
    fillet = FilletBuilder().add_edge("edge-fillet")
    chamfer = ChamferBuilder().add_edge("edge-chamfer")
    shell = ShellBuilder().add_face("face-shell")
    linear = LinearPatternBuilder(direction_edge_id="edge-direction").add_feature(
        "feature-linear"
    )
    circular = CircularPatternBuilder(axis_entity_id="axis-circular").add_feature(
        "feature-circular"
    )
    boolean = BooleanBuilder().add_tool_body("body-tool")
    return [
        ExtrudeBuilder(sketch_feature_id="sketch-extrude").build(),
        fillet.build(),
        chamfer.build(),
        shell.build(),
        OffsetPlaneBuilder(reference_id="plane-reference").build(),
        linear.build(),
        circular.build(),
        boolean.build(),
        serialize_constraint("HORIZONTAL", entity="line-1"),
    ]


@pytest.mark.parametrize("payload", _affected_payloads())
def test_affected_payloads_omit_rejected_library_relation_type(payload):
    assert not _contains_key(payload, "libraryRelationType")


def test_extrude_keeps_evidence_backed_parameter_shape_without_end_bound():
    feature = ExtrudeBuilder(sketch_feature_id="sketch-1", depth="12 mm").build()[
        "feature"
    ]
    parameters = {item["parameterId"]: item for item in feature["parameters"]}

    assert feature["featureType"] == "extrude"
    assert set(parameters) == {
        "entities",
        "operationType",
        "depth",
        "oppositeDirection",
        "symmetric",
    }
    region_query = parameters["entities"]["queries"][0]
    assert region_query["btType"] == "BTMIndividualSketchRegionQuery-140"
    assert region_query["featureId"] == "sketch-1"
    assert 'qSketchRegion(id + "sketch-1", true)' in region_query["queryString"]
    assert parameters["depth"]["expression"] == "12 mm"
    assert "endBound" not in parameters
