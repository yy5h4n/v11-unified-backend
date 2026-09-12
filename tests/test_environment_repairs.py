from pathlib import Path
import pytest
from d2_fds_adapter import FDSSmokePropagationAdapter, _interactive_model_text
from unified_compiler.route_registry import route_metadata


def test_fds_new_command_cannot_be_interpolated_into_past():
    text=_interactive_model_text([(0.,0.),(10.,1.)],20.)
    assert "T=10.000000000, F=1.0" in text
    assert "T=10.000001000, F=-1.0" in text
    assert 'T_END=20.000000000' in text


def test_fds_native_horizon_matches_registry():
    assert FDSSmokePropagationAdapter().horizon_seconds==route_metadata('fds_smoke_fire')['horizon_seconds']==60.
    for horizon in [True,0.,-1.,float('nan')]:
        with pytest.raises(ValueError):FDSSmokePropagationAdapter(horizon_seconds=horizon)


def test_native_room_loss_requires_continued_control():
    from tools.verify_repaired_thermal import rollout
    root=Path(__file__).resolve().parents[1]/'environment_repairs_v1/build'
    assert (root/'d2/active/modelDescription.xml').exists(), 'rebuild native FMU before running'
    idle=rollout('modelica_buildings_aixlib','idle')
    controlled=rollout('modelica_buildings_aixlib','feedback')
    assert not idle['passed']
    assert controlled['passed']


def test_native_shared_heat_has_real_service_demand():
    from tools.verify_repaired_thermal import rollout
    root=Path(__file__).resolve().parents[1]/'environment_repairs_v1/build'
    assert (root/'d3/active/modelDescription.xml').exists(), 'rebuild native FMU before running'
    idle=rollout('d3_modelica_shared_heat','idle')
    controlled=rollout('d3_modelica_shared_heat','feedback')
    assert idle['min_hot_water_c']<40
    assert not idle['passed']
    assert controlled['passed']
