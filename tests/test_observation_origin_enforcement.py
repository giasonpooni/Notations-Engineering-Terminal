"""Observed projections respect retained measurement and derived-channel semantics."""
from unittest.mock import patch

import pytest

from ciw.adapters.protocol import InstrumentManifest
from ciw.control_plane import observations_from_run
from ciw.core.identities import evidence_id


def source_record(*, kind="observation", provenance=None):
    manifest = InstrumentManifest("test.measurement-origin.v1", role="measurement_adapter",
                                  units={"mass": "kg"}, frames=("sensor-frame",))
    run = {"run_schema": "run.v1", "run_id": "measurement-origin-fixture",
           "instrument": manifest.instrument_id, "time_s": [0.0], "render": {},
           "channels": {"mass": {"unit": "kg", "values": [1.0], "kind": kind}},
           "metadata": {"duration_s": 1.0, "sample_count": 1, "coordinate_frame": "sensor-frame",
                        "manifest": manifest.to_dict(),
                        "provenance": {"semantics": "observed", **(provenance or {})}}}
    run["evidence_id"] = evidence_id(run)
    return run


def project(run):
    return observations_from_run(run, channel="mass", entity_id="sensor", clock_id="clock",
                                 model_id="sensor.v1", semantics="observed")


@pytest.mark.parametrize("kind", ["observation", "calibrated_observation"])
def test_existing_measurement_adapter_channel_kinds_export_without_execution(kind):
    run = source_record(kind=kind)
    # Reading declared source records neither runs an operation nor loads a
    # scientific provider. Existing structural adapter validation remains.
    with patch("ciw.adapters.registry.AdapterRegistry.execute", side_effect=AssertionError("provider executed")), \
         patch("ciw.operations.runner.execute", side_effect=AssertionError("operation executed")), \
         patch("ctypes.CDLL", side_effect=AssertionError("native library loaded")), \
         patch("subprocess.Popen", side_effect=AssertionError("process launched")):
        projected = project(run)
    assert projected[0]["value"] == 1.0
    assert projected[0]["provenance"]["semantics"] == "observed"
    assert projected[0]["identity"]["execution_id"] is None


@pytest.mark.parametrize("kind", ["estimated_state", "simulated_observation", "declared_initial_condition"])
def test_run_level_observed_claim_cannot_relabel_derived_channel(kind):
    with pytest.raises(ValueError, match="Observed projection requires"):
        project(source_record(kind=kind))


@pytest.mark.parametrize("marker,value", [
    ("origin", "synthetic"), ("origin", "computed_model_output"),
    ("source_class", "synthetic"), ("source_class", "synthetic_fixture"),
    ("source_class", "simulated_observation"),
])
def test_known_structured_nonobserved_origin_cannot_be_overridden(marker, value):
    with pytest.raises(ValueError, match="Observed projection requires"):
        project(source_record(provenance={marker: value}))


@pytest.mark.parametrize("marker", ["semantics", "kind", "origin", "source_class"])
@pytest.mark.parametrize("value", [[], {}, False, None, ""])
def test_malformed_scalar_origin_marker_refuses_with_value_error(marker, value):
    with pytest.raises(ValueError, match="Observed projection marker"):
        project(source_record(provenance={marker: value}))


@pytest.mark.parametrize("marker", ["observed", "synthetic"])
@pytest.mark.parametrize("value", [[], {}, 0, 1, "false", None])
def test_origin_flags_require_actual_booleans(marker, value):
    with pytest.raises(ValueError, match="must be a boolean"):
        project(source_record(provenance={marker: value}))


@pytest.mark.parametrize("value", [[], {}, False, None, ""])
def test_malformed_channel_kind_refuses_with_value_error(value):
    with pytest.raises(ValueError, match="Observed projection marker"):
        project(source_record(kind=value))


@pytest.mark.parametrize("description", [{"dataset": "acquisition-1"}, ["sensor-log", "batch-1"]])
def test_structured_source_description_is_not_interpreted_as_a_semantic_marker(description):
    run = source_record(provenance={"source": description})
    projected = project(run)
    assert projected[0]["provenance"]["sources"] == [run["evidence_id"]]

@pytest.mark.parametrize("marker", ["origin", "source_class"])
@pytest.mark.parametrize("value", ["analytic", "synthetic", "simulated", "estimated", "reference", "computed"])
def test_all_retained_nonobserved_origin_markers_refuse_promotion(marker, value):
    with pytest.raises(ValueError, match="Observed projection requires"):
        project(source_record(provenance={marker: value}))
