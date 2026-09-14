import json
import threading
from copy import deepcopy
from http.server import ThreadingHTTPServer
from pathlib import Path

from chamber import workflow
from chamber.environment.chambers import ChamberStore, ChamberProfile
from tests.test_chambers_experiments import Controller, config_for
from tests.test_load_suite import Target, traffic
from tests.test_relayna_traffic import _journey
from tests.test_phase11_12_13_workflow import _attach_kubernetes_config, _attach_runner, _fixture_repo

root = Path('/data/load-runtime')
root.mkdir(exist_ok=True)
repo = _fixture_repo(root)
server = ThreadingHTTPServer(('127.0.0.1', 0), Target)
threading.Thread(target=server.serve_forever, daemon=True).start()
config = _attach_kubernetes_config(repo)
config['runtime']['trafficAccess'] = {'mode':'endpoint', 'url':f'http://127.0.0.1:{server.server_port}'}
config['runtime'].pop('prometheusUrl', None)
config['traffic'].update(traffic(ratePerSecond=10, durationSeconds=2, maxInFlight=16, timeoutSeconds=2, thresholds={'p99Ms':1500}))
config['traffic']['journeys'].append({**_journey(), 'method':'POST', 'weight':2})
config['experiment'] = config_for('cpu_pressure')['experiment']
config['experiment']['recoverySeconds'] = 5
config['assumptions'] = ['SIMULATED Kubernetes controller; real loopback HTTP/SSE load, no live cluster fault injection.']
path = root / 'chamber.yaml'
workflow.save_config(config, path)
base, controller = _attach_runner(), Controller()
class Runner:
    def run(self, command, input_text=None):
        if any('chaos-mesh.org/' in arg for arg in command) or 'create' in command or ('pod' in command and any(pod in command for pod in ('api-0','redis-0'))):
            return controller.run(command)
        return base.run(command)
run = workflow._assess_kubernetes_config(path, agents_mode='off', context='dev-cluster', prometheus_url=None,
    runner=Runner(), run_dir=Path('/data/workspace/runs/load-suite-runtime-fixture'))
result = json.loads((run/'result.json').read_text())
assert result['load']['status'] == 'pass', result['load']
assert [phase['phase'] for phase in result['load']['phases']] == ['baseline','recovery']
assert result['load']['windows'][0]['phase'] == 'fault'
ChamberStore(Path('/data/workspace')).create(ChamberProfile(name='Load suite fixture', context='dev-cluster',namespace=config['runtime']['namespace'],service=config['service']['name'],workload=config['deployment']['workloads'][0]['name'], max_duration_seconds=7200))
server.shutdown()
print(json.dumps({'run':str(run), 'loadStatus':result['load']['status'], 'metrics':result['load']['metrics'], 'phases':[p['phase'] for p in result['load']['phases']], 'reportStatus':result['status']},indent=2))
