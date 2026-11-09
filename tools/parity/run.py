#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import argparse
import configparser
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

NETWORK_SETUP = (
    'ip link set lo up && ip link add parity0 type dummy && '
    'ip address add 192.0.2.1/32 dev parity0 && '
    'ip link set parity0 multicast on up && '
    'ip route add 224.0.0.251/32 dev parity0 src 192.0.2.1 && exec "$@"'
)


def resources():
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    psi = Path('/proc/pressure/memory').read_text()
    return {
        'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'available_bytes': int(memory['MemAvailable'].split()[0]) * 1024,
        'swaps': Path('/proc/swaps').read_text(),
        'memory_psi': psi,
        'psi_full_avg10': float(psi.split('full avg10=')[1].split()[0]),
    }


def group_members(pgid):
    members = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == pgid and fields[0] != 'Z':
                members.append(int(entry.name))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return members


def stop_owned_group(process):
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in handlers:
        signal.signal(sig, signal.SIG_IGN)
    try:
        return stop_group(process)
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def stop_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 5
    while group_members(process.pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if group_members(process.pid):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)
    return group_members(process.pid)


def command_for(args, binary):
    return ['unshare', '--user', '--map-root-user', '--net', 'sh', '-c',
            NETWORK_SETUP, 'parity-isolated', str(args.node),
            str(Path(__file__).with_name('runtime_checks.cjs')),
            str(args.source), str(binary), str(args.fixture)]


def provenance(binary, explicit_record):
    files = [binary.parent / 'application.ini', binary.parent / 'platform.ini',
             binary.parent.parent / 'SHA256SUMS', binary.parent.parent / 'firefox.tar.xz.sha256']
    if explicit_record:
        files.append(explicit_record)
    else:
        files.append(binary.parent.parent / 'source-record.json')
    result = {}
    for path in files:
        if path.is_file():
            if path.stat().st_size > 1024 * 1024:
                raise RuntimeError(f'provenance file exceeds 1MiB: {path.name}')
            if path.suffix == '.ini':
                ini = configparser.ConfigParser(interpolation=None)
                ini.read(path)
                allowed = {'vendor', 'name', 'version', 'buildid', 'sourcerepository', 'sourcestamp',
                           'id', 'milestone', 'minversion', 'maxversion'}
                result[str(path)] = {section: {key: value for key, value in ini.items(section) if key in allowed}
                                     for section in ['App', 'Build', 'Gecko'] if ini.has_section(section)}
            elif path.suffix == '.json':
                record = json.loads(path.read_text())
                result[str(path)] = {name: record[name] for name in
                                     ['source_revision', 'base', 'wpt_tree', 'tracked_clean'] if name in record}
            else:
                result[str(path)] = path.read_text()
    return {'existing_records': result,
            'limit': 'Records are copied, not verified against newly hashed binary bytes; harness revision is not engine revision.'}


def run_target(args, label, binary, source_record, output, report, deadline, owned=None):
    temp = output / (label + '-tmp')
    temp.mkdir()
    home = temp / 'home'
    home.mkdir()
    env = {'PATH': os.defpath, 'HOME': str(home), 'TMPDIR': str(temp),
           'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'TZ': 'Europe/Berlin',
           'PARITY_TEST_HOST': '192.0.2.1'}
    command = command_for(args, binary)
    item = {'label': label, 'binary': str(binary), 'command': command,
            'runtime_provenance': provenance(binary, source_record)}
    report['runs'].append(item)
    process = None
    started = time.monotonic()
    with (output / (label + '.json')).open('x') as stdout, (output / (label + '.stderr')).open('x') as stderr:
        try:
            process = subprocess.Popen(command, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
            if owned is not None:
                owned.append(process)
            item['owned_process_group'] = process.pid
            while process.poll() is None:
                sample = resources()
                report['resources'].append(sample)
                if sample['available_bytes'] < 3 * 1024**3 or sample['psi_full_avg10'] > 5:
                    raise RuntimeError('memory pressure guard stopped owned process group')
                if time.monotonic() >= min(started + 75, deadline):
                    raise RuntimeError('runtime time bound exceeded')
                time.sleep(0.25)
        finally:
            if process:
                item['remaining_owned_processes'] = stop_owned_group(process)
                item['returncode'] = process.returncode
            item['elapsed_seconds'] = time.monotonic() - started
    if item.get('remaining_owned_processes'):
        raise RuntimeError('owned process group cleanup incomplete')
    if item['returncode']:
        raise RuntimeError(f'{label} failed; subsequent browser not launched')
    result = json.loads((output / (label + '.json')).read_text())
    checks = result.get('checks')
    if not isinstance(checks, list) or not checks or any(check.get('status') != 'pass' for check in checks):
        raise RuntimeError(f'{label} did not produce complete passing structured checks')
    required = {'webrtc-local-data-transport', 'indexeddb-transaction-after-reload',
                'unsigned-permanent-addon-rejected', 'runtime-configuration-inventory-collected',
                'required-security-feature-configuration'}
    names = [check.get('name') for check in checks]
    if set(names) != required or len(names) != len(required):
        raise RuntimeError(f'{label} structured result must contain every required check exactly once')


def cancelled(signum, frame):
    raise KeyboardInterrupt(f'received signal {signum}')


def settle_owned_processes(owned, report, output):
    while True:
        report['remaining_owned_processes'] = {str(process.pid): group_members(process.pid) for process in owned}
        report['unreaped_leaders'] = [process.pid for process in owned if process.poll() is None]
        report['cleanup_pending'] = bool(any(report['remaining_owned_processes'].values()) or report['unreaped_leaders'])
        try:
            (output / 'resource-cleanup.json').write_text(json.dumps(report, indent=2) + '\n')
        except OSError as error:
            report['cleanup_evidence_error'] = str(error)
            report.setdefault('error', 'cleanup evidence write failed; retaining ownership until cleanup completes')
        if not report['cleanup_pending']:
            return
        report.setdefault('error', 'owned process cleanup pending; resource lock retained')
        time.sleep(1)


def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'schema': 1, 'network': 'isolated user/network namespace, local dummy192.0.2.1/32, mDNS224.0.0.251/32 only, no default route',
              'runs': [], 'resources': [], 'source_mutated': False,
              'output': str(args.output), 'lock_acquired': False, 'lock_released': False}
    lock = None
    owned = []
    saved_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in saved_handlers:
        signal.signal(sig, cancelled)
    try:
        lock = (args.root / 'resource.lock').open('a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report['lock_acquired'] = True
        report['resources'].append(resources())
        initial = report['resources'][0]
        if initial['available_bytes'] < 6 * 1024**3 or initial['psi_full_avg10'] > 1:
            raise RuntimeError('initial memory/pressure guard rejected runtime')
        area = Path(__file__).resolve().parent
        files = [area / 'runtime_checks.cjs', Path(__file__).resolve(), args.fixture]
        report['harness_sha256'] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        deadline = time.monotonic() + 150
        for label, binary, record in [('official', args.official, args.official_source_record),
                                      ('custom', args.custom, args.custom_source_record)]:
            if time.monotonic() >= deadline:
                raise RuntimeError('total workload time bound reached before next browser')
            run_target(args, label, binary, record, args.output, report, deadline, owned=owned)
    except (Exception, KeyboardInterrupt) as error:
        report['error'] = str(error)
        report['cancelled'] = isinstance(error, KeyboardInterrupt)
    finally:
        for sig in saved_handlers:
            signal.signal(sig, signal.SIG_IGN)
        settle_owned_processes(owned, report, args.output)
        try:
            report['resources'].append(resources())
        except Exception as error:
            report['resource_capture_error'] = str(error)
        (args.output / 'resource-cleanup.json').write_text(json.dumps(report, indent=2) + '\n')
        if lock:
            lock.close()
            report['lock_released'] = report['lock_acquired']
        (args.output / 'resource-cleanup.json').write_text(json.dumps(report, indent=2) + '\n')
        for sig, handler in saved_handlers.items():
            signal.signal(sig, handler)
    print(args.output)
    return int('error' in report or 'resource_capture_error' in report or any(report['remaining_owned_processes'].values()))


def arguments():
    parser = argparse.ArgumentParser(description='Run sequential offline Firefox parity checks on Linux.')
    for name in ['root', 'source', 'official', 'custom', 'output']:
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--node', type=Path, default=shutil.which('node'))
    parser.add_argument('--fixture', type=Path, default=Path(__file__).with_name('unsigned-fixture.xpi'))
    parser.add_argument('--official-source-record', type=Path)
    parser.add_argument('--custom-source-record', type=Path)
    args = parser.parse_args()
    if not args.node:
        parser.error('node not found; pass --node')
    try:
        args.node = native_node(args.node)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    for name in ['root', 'source', 'official', 'custom', 'fixture', 'official_source_record', 'custom_source_record']:
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.resolve(strict=True))
    args.output = args.output.resolve()
    return args


def native_node(path):
    path = path.resolve(strict=True)
    with path.open('rb') as stream:
        native = stream.read(4) == b'\x7fELF'
    if not native or path.name not in ('node', 'nodejs') or not os.access(path, os.X_OK):
        raise ValueError('Node dispatchers/shims are unsupported with the isolated HOME; pass --node a native ELF Node executable named node or nodejs from the installed toolchain')
    return path


if __name__ == '__main__':
    raise SystemExit(run(arguments()))
