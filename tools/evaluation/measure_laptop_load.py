"""Five-minute supervised health baseline, not a student/chat capacity claim."""
import ctypes
import argparse
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.request import urlopen
import winreg

ROOT = Path(__file__).resolve().parents[2]


class Memory(ctypes.Structure):
    _fields_ = [('length', wintypes.DWORD), ('load', wintypes.DWORD)] + [
        (name, ctypes.c_ulonglong) for name in ('total', 'available', 'page_total',
        'page_available', 'virtual_total', 'virtual_available', 'extended')]


def sample():
    memory = Memory()
    memory.length = ctypes.sizeof(memory)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        raise ctypes.WinError()
    times = [wintypes.FILETIME() for _ in range(3)]
    if not ctypes.windll.kernel32.GetSystemTimes(*(ctypes.byref(t) for t in times)):
        raise ctypes.WinError()
    ticks = [(t.dwHighDateTime << 32) | t.dwLowDateTime for t in times]
    return dict(time_utc=datetime.now(timezone.utc).isoformat(), ram_bytes=memory.total,
                available_bytes=memory.available, memory_percent=memory.load,
                idle=ticks[0], total=ticks[1] + ticks[2])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-vus', nargs='+', type=int)
    args = parser.parse_args()
    if args.confirm_vus and (len(args.confirm_vus) > 4 or any(v < 1 for v in args.confirm_vus)):
        parser.error('supply one to four positive VU counts')
    out = ROOT / 'evaluation/test_runs' / ('laptop-load-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    out.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
        cpu = winreg.QueryValueEx(key, 'ProcessorNameString')[0].strip()
    result = dict(scope='Local single-worker /health only, not authenticated students or AI chat',
                  cpu=cpu, logical_cpus=os.cpu_count(), resources_before=sample(), api_url=url,
                  database='Isolated SQLite; health does not query it', think_seconds=1,
                  p95_limit_ms=1500, allowed_errors=0, load_budget_seconds=300,
                  trials=[], confirmed_vus=None)
    def save():
        (out / 'report.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    stop = threading.Event()
    def monitor():
        previous = sample()
        with (out / 'resources.jsonl').open('w', encoding='utf-8') as stream:
            while not stop.wait(1):
                row = sample()
                total = row['total'] - previous['total']
                row['cpu_percent'] = 100 * (1 - (row['idle'] - previous['idle']) / total) if total else None
                stream.write(json.dumps(row) + '\n')
                stream.flush()
                previous = row
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'apps/api'), PYTHONIOENCODING='utf-8',
               CURRICULUM_TUTOR_DATABASE_URL='sqlite:///' + str(out / 'isolated.sqlite3').replace('\\', '/'))
    server = None
    thread = None
    print(f'Results: {out}\nCPU: {cpu}; logical CPUs: {os.cpu_count()}', flush=True)
    with (out / 'api.log').open('w', encoding='utf-8') as log:
        try:
            server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host',
                '127.0.0.1', '--port', str(port), '--workers', '1'], cwd=ROOT, env=env,
                stdout=log, stderr=subprocess.STDOUT)
            ready_until = time.monotonic() + 90
            while True:
                if server.poll() is not None:
                    raise RuntimeError('API exited; inspect api.log')
                try:
                    with urlopen(url + '/health', timeout=2) as response:
                        if json.load(response).get('status') == 'ok':
                            break
                except OSError:
                    pass
                if time.monotonic() > ready_until:
                    raise RuntimeError('API startup timeout')
                time.sleep(1)
            thread = threading.Thread(target=monitor, daemon=True)
            thread.start()
            started = time.monotonic()
            deadline = started + 300
            def probe(vus, seconds, phase):
                stem = f'{len(result["trials"])+1:02d}_{phase}_{vus}vu'
                summary = out / (stem + '.json')
                probe_env = dict(os.environ, K6_API_BASE_URL=url, K6_VUS=str(vus), K6_DURATION=f'{seconds}s', K6_NO_USAGE_REPORT='true')
                print(f'{phase}: {vus} VUs for {seconds}s', flush=True)
                began = time.monotonic()
                with (out / (stem + '.log')).open('w', encoding='utf-8') as probe_log:
                    try:
                        process = subprocess.run([shutil.which('k6') or r'C:\Program Files\k6\k6.exe',
                            'run', '--summary-export', str(summary), str(ROOT / 'tests/system/load/health_capacity.js')],
                            env=probe_env, cwd=ROOT, stdout=probe_log, stderr=subprocess.STDOUT,
                            timeout=max(1, min(seconds + 35, deadline - time.monotonic())))
                        code = process.returncode
                    except subprocess.TimeoutExpired:
                        code = None
                row = dict(vus=vus, phase=phase, target_seconds=seconds,
                           wall_seconds=round(time.monotonic()-began, 3), exit_code=code, status='inconclusive')
                if summary.exists():
                    metrics = json.loads(summary.read_text(encoding='utf-8'))['metrics']
                    row.update(p95_ms=metrics['http_req_duration']['p(95)'],
                        failure_rate=metrics['http_req_failed']['value'], check_rate=metrics['checks']['value'],
                        requests=metrics['http_reqs']['count'], rps=metrics['http_reqs']['rate'],
                        max_vus=metrics.get('vus', {}).get('max'))
                    if row['max_vus'] == vus and row['requests'] >= vus * 3:
                        row['status'] = 'pass' if code == 0 and row['failure_rate'] == 0 and row['check_rate'] == 1 and row['p95_ms'] < 1500 else 'fail'
                result['trials'].append(row)
                save()
                print(json.dumps(row), flush=True)
                return row['status']
            low, high, candidate = 0, None, 25
            while not args.confirm_vus and time.monotonic() + 80 < deadline and len(result['trials']) < 10:
                status = probe(candidate, 20, 'search')
                if status == 'inconclusive':
                    result['search_interrupted_reason'] = 'Incomplete generator or measurement'
                    break
                if status == 'pass':
                    low = candidate
                else:
                    high = candidate
                if high is None:
                    candidate *= 2
                elif high - low <= 5:
                    break
                else:
                    candidate = (low + high) // 2
            result.update(passing_lower_bound=low or None, failing_upper_bound=high)
            for count in args.confirm_vus or ([low] if low else []):
                if time.monotonic() + 50 < deadline and probe(count, 35, 'confirmation') == 'pass':
                    result['confirmed_vus'] = max(count, result['confirmed_vus'] or 0)
            result['load_elapsed_seconds'] = round(time.monotonic() - started, 3)
            try:
                with urlopen(url + '/health', timeout=5) as response:
                    result['post_load_health_ok'] = json.load(response).get('status') == 'ok'
            except OSError:
                result['post_load_health_ok'] = False
        except Exception as exc:
            result['error'] = str(exc)
        finally:
            stop.set()
            if thread:
                thread.join(timeout=3)
            if server and server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
            result['api_stopped'] = server is None or server.poll() is not None
            save()
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result['confirmed_vus'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
