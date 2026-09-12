"""Fresh public-vs-native source checks, not task/evaluator verification."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from unified_compiler.agent_interface import make_agent_backend
from unified_compiler.llm_conversation import canonical_json
from tools.backend_acceptance_runner import _action

ROUTES = ('d0_exogenous_context', 'd1_discrete_device_fault', 'd1_sustaingym_fault',
          'energyplus_iaq', 'd3_energyplus_shared_ventilation',
          'modelica_buildings_aixlib', 'd3_modelica_shared_heat', 'fds_smoke_fire',
          'wntr_residential_water', 'd3_wntr_water_competition',
          'd1_citylearn_battery_fault', 'd3_citylearn_multi_system',
          'd3_citylearn_multibuilding_competition', 'd1_ev2gym_fault',
          'd3_ev2gym_electric_competition')


def reference(route, n):
    if route == 'd1_ev2gym_fault':
        env = n.base._runtime.env
        ev = env.charging_stations[0].evs_connected[0]
        return {'vehicle_connected': ev is not None,
                'vehicle_soc': None if ev is None else round(float(ev.get_soc()), 6)}, 'native connected EV state; public six-decimal SOC precision'
    if route == 'd3_ev2gym_electric_competition':
        env = n.env
        ports = []
        for charger in env.charging_stations[:2]:
            ev = charger.evs_connected[0] if charger.evs_connected else None
            ports.append({'connected': ev is not None,
                          'soc': None if ev is None else float(ev.get_soc()),
                          'power_kw': float(charger.current_power_output),
                          'max_power_kw': float(charger.get_max_power()),
                          'scheduled_departure_step': None if ev is None else int(ev.time_of_departure),
                          'battery_capacity_kwh': None if ev is None else float(ev.battery_capacity),
                          'required_departure_kwh': None if ev is None else float(ev.desired_capacity)})
        t = env.transformers[0]
        return {'ports': ports, 'transformer': {'power_kw': float(t.current_power),
                'capacity_kw': float(t.max_power[min(t.current_step, len(t.max_power)-1)])}}, 'native charger EV objects and transformer state; initial two steps may have no connected EV'
    if route == 'd1_citylearn_battery_fault':
        index = n._last_completed_native_index
        if index is None:
            index = n.battery.time_step
        return {'battery_soc': float(n.battery.soc[index])}, 'native battery SOC buffer at last completed interval'
    if route in {'d3_citylearn_multi_system', 'd3_citylearn_multibuilding_competition'}:
        # These probes stop after two transitions, before the terminal index
        # can stop advancing. Initial warm-up also has a completed interval.
        index = max(0, n.env.time_step - 1)
        values = {}
        for b in n.env.buildings:
            prefix = b.name + '.' if route.endswith('multibuilding_competition') else ''
            values.update({prefix+'electrical_storage_soc': float(b.electrical_storage.soc[index]),
                           prefix+'indoor_dry_bulb_temperature': float(b.indoor_dry_bulb_temperature[index]),
                           prefix+'net_electricity_consumption': float(b.net_electricity_consumption[index])})
        return values, 'native building completed-interval SOC, temperature and electricity arrays'
    if route in {'energyplus_iaq', 'modelica_buildings_aixlib', 'fds_smoke_fire', 'wntr_residential_water'}:
        n = n.backend  # D2 public protocol wrapper, not the native object.
    if route in {'wntr_residential_water', 'd3_wntr_water_competition'}:
        before = n.observe()
        request = n._session_request if route == 'wntr_residential_water' else n._request
        data = request({'command': 'inspect_native'})
        if canonical_json(before) != canonical_json(n.observe()):
            raise ValueError('private read changed public observation')
        raw = data['native']
        if route == 'wntr_residential_water':
            values = {'pressure_kitchen_m': raw['pressure']['kitchen'], 'pressure_bathroom_m': raw['pressure']['bathroom'],
                'flow_source_fill_m3_s': raw['flowrate']['source_fill'], 'flow_house_isolation_m3_s': raw['flowrate']['house_isolation'],
                'tank_level_m': raw['head']['house_tank']-25., 'leak_kitchen_m3_s': raw['leak_demand']['kitchen'],
                'leak_bathroom_m3_s': raw['leak_demand']['bathroom'],
                'leak_total_m3_s': raw['leak_demand']['kitchen']+raw['leak_demand']['bathroom']}
        else:
            values = {'pressure_shower_m': raw['pressure']['shower'], 'pressure_laundry_m': raw['pressure']['laundry'],
                'flow_shower_m3_s': raw['flowrate']['shower_service'], 'flow_laundry_m3_s': raw['flowrate']['laundry_service'],
                'flow_source_fill_m3_s': raw['flowrate']['source_fill'], 'tank_level_m': raw['head']['house_tank']-25.,
                'served_shower_m3_s': raw['leak_demand']['shower'], 'served_laundry_m3_s': raw['leak_demand']['laundry']}
            values = {key: round(value, 12) for key, value in values.items()}
        return values, 'read-only raw native WNTR node/link result tables'
    if route == 'd0_exogenous_context':
        return {'context': deepcopy(n.context), 'devices': deepcopy(n.devices), 'step': n.step_index}, 'native discrete state'
    if route == 'd1_discrete_device_fault':
        return {'devices': {key: {'state': value['state']} for key, value in n._devices.items()}}, 'native workflow device states'
    if route == 'd1_sustaingym_fault':
        ep = n._episode
        base = ep.base
        vector = base._env.state
        size = int(base._env.n)
        sensor = ep.sensors.state_at(ep._step_index)
        offset = sensor.correction(ep._step_index) if sensor else 0.
        return {'zone_temperatures_c': [float(v)+offset for v in vector[:size]],
                'weather': {'outdoor_temperature_c': float(vector[size]), 'ground_temperature_c': float(vector[size+1])},
                'occupancy': {'occupancy_power_kw': float(vector[size+3])}}, 'native state vector plus declared current sensor fault'
    if route == 'energyplus_iaq':
        from d2_humidity_air_quality_adapter import OBSERVABLES
        with n._session_cv:
            values = {key: float(n._session_api.exchange.get_variable_value(n._session_state, n._session_handles[key])) for key in OBSERVABLES}
        return values, 'direct EnergyPlus exchange at paused callback barrier'
    if route == 'd3_energyplus_shared_ventilation':
        names = {f'zone_{z}_{field}': f'zone_{z}_{handle}' for z in ('a', 'b') for field, handle in (
            ('co2_ppm', 'co2'), ('relative_humidity_pct', 'rh'), ('temperature_c', 'temp'), ('actual_airflow_m3_s', 'airflow'))}
        with n._cv:
            values = {key: float(n._api.exchange.get_variable_value(n._state, n._handles[handle])) for key, handle in names.items()}
        return values, 'direct EnergyPlus exchange at paused callback barrier'
    if route == 'modelica_buildings_aixlib':
        s = n._session
        names = {'heater_heat_flow_w': 'heaterHeatFlow', 'room_a_relative_humidity_pct': 'roomARelativeHumidity',
                 'room_a_temperature_c': 'roomATemperature', 'room_b_relative_humidity_pct': 'roomBRelativeHumidity',
                 'room_b_temperature_c': 'roomBTemperature'}
        refs = (s._ValueReference * len(names))(*(s.variable_refs[v] for v in names.values()))
        values = (s._Real * len(names))()
        s._check(s._get_real(s._component, refs, len(names), values), 'source-check fmi2GetReal')
        return dict(zip(names, map(float, values))), 'direct FMI value references'
    if route == 'd3_modelica_shared_heat':
        from d3_modelica_shared_heat_adapter import OBSERVATION_NAMES
        s = n._session
        refs = (s.VR * len(OBSERVATION_NAMES))(*(s.refs[v] for v in OBSERVATION_NAMES))
        values = (s.Real * len(OBSERVATION_NAMES))()
        s._check(s.get_real(s.component, refs, len(OBSERVATION_NAMES), values), 'source-check fmi2GetReal')
        return dict(zip(OBSERVATION_NAMES, map(float, values))), 'direct FMI value references'
    if route == 'fds_smoke_fire':
        row = n._interactive_trace[0] if n._interactive_time_s == 0 else n._interactive_trace[-1]
        return deepcopy(row['observation']), 'parsed native FDS device row (parser itself not independently tested here)'
    raise ValueError(route)


def matches(public, expected):
    if isinstance(expected, dict):
        return isinstance(public, dict) and all(k in public and matches(public[k], v) for k, v in expected.items())
    return canonical_json(public) == canonical_json(expected)


def probe(route, *, seed=0, steps=2):
    if steps < 2:
        raise ValueError('source checks require at least two transitions')
    backend = make_agent_backend(route)
    evidence = []
    try:
        initial = backend.reset(seed=seed)
        legal = backend.legal_actions()
        receipts = [initial]
        for i in range(steps + 1):
            r = receipts[-1]
            expected, source = reference(route, backend.route)
            evidence.append({'time_seconds': r['time_seconds'], 'public_observation': r['observation'],
                             'native_values': expected, 'source': source, 'passed': matches(r['observation'], expected)})
            if i < steps:
                receipts.append(backend.step(_action(route, legal, i % 2)))
        return {'route_id': route, 'status': 'passed_scoped_source_check' if all(r['passed'] for r in evidence) else 'failed',
                'seed': seed, 'transitions_checked': steps,
                'evidence': evidence, 'scope': f'seed {seed} reset plus {steps} transitions; selected native channels, not all physical correctness'}
    finally:
        backend.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--route', choices=ROUTES, required=True)
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--steps', type=int, default=2)
    p.add_argument('--output-dir', type=Path, default=ROOT/'generated/observation_sources_v1')
    args = p.parse_args()
    try:
        report = probe(args.route, seed=args.seed, steps=args.steps)
    except Exception as exc:
        report = {'route_id': args.route, 'status': 'runtime_error', 'error': str(exc)}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir/(args.route+'.json')).write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'evidence'}))
    raise SystemExit(0 if report['status'] == 'passed_scoped_source_check' else 1)
