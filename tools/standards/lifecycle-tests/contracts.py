import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
DEFAULT_MODULE = HERE.parent


def health():
    m = {line.split(':')[0]: line.split(':')[1].strip() for line in Path('/proc/meminfo').read_text().splitlines()}
    psi = float(Path('/proc/pressure/memory').read_text().splitlines()[1].split()[1].split('=')[1])
    if int(m['MemAvailable'].split()[0]) < 5 * 1024 * 1024 or psi >= 1:
        raise RuntimeError('host resource guard; stop fixture batch')
    return {'available_kib': int(m['MemAvailable'].split()[0]), 'full_psi_avg10': psi}


def stat(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return {'pid': pid, 'starttime': int(fields[19]), 'state': fields[0], 'parent': int(fields[1])}
    except (FileNotFoundError, ProcessLookupError):
        return None


def alive(actor):
    current = stat(actor['pid'])
    return bool(current and current['starttime'] == actor['starttime'] and current['state'] != 'Z')


def direct_children(pid):
    children = set()
    try:
        tasks = list(Path(f'/proc/{pid}/task').iterdir())
    except FileNotFoundError:
        return children
    for task in tasks:
        try:
            children.update(int(x) for x in (task / 'children').read_text().split())
        except FileNotFoundError:
            pass
    return children


def rss(pid):
    try:
        return next(int(line.split()[1]) for line in Path(f'/proc/{pid}/status').read_text().splitlines() if line.startswith('VmRSS:'))
    except (FileNotFoundError, ProcessLookupError, StopIteration):
        return 0


class Case:
    def __init__(self, output, name, module, mode, injection='none'):
        self.root = output / name
        self.root.mkdir()
        self.module, self.mode, self.injection = module, mode, injection
        self.children = {}
        self.events = []
        self.peak_rss = 0
        self.started = time.monotonic()
        self.proc = None
        self.sentinel = None
        self.tmp = Path(tempfile.mkdtemp(prefix='lc-', dir='/tmp'))
        (self.root / 'tmp-root.txt').write_text(str(self.tmp))
        tests = self.root / 'source/testing/web-platform/tests'
        tests.mkdir(parents=True)
        (tests / 'case.html').write_text('SYNTHETIC EXTERNAL-BOUNDARY FIXTURE')
        (self.root / 'selection.txt').write_text('case.html\n')
        (self.root / 'MANIFEST.json').write_text('{}')
        binary = self.root / 'fake-binary'
        if mode == 'setup':
            script = 'import runpy, sys\nsys.argv = ' + repr([str(HERE / 'fixture.py'), 'tree', str(self.root), 'wait']) + '\nrunpy.run_path(sys.argv[0], run_name="__main__")\n'
        else:
            script = 'print("SYNTHETIC fake version")\n'
        binary.write_text('#!' + sys.executable + '\n' + script)
        binary.chmod(0o700)
        (self.root / 'resource.lock').touch()
        self.lock_inode = (self.root / 'resource.lock').stat().st_ino
        if mode=='foreign-output':
            (self.root/'run').mkdir()
            (self.root/'run/lifecycle.json').write_text('FOREIGN-OUTPUT-SENTINEL')

    def actors(self):
        path = self.root / 'actors.jsonl'
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def capture(self, enforce_guard=True):
        roots = [os.getpid()]
        seen = set()
        while roots:
            pid = roots.pop()
            if pid in seen:
                continue
            seen.add(pid)
            for child in direct_children(pid):
                current = stat(child)
                if current:
                    self.children.setdefault(child, current)
                    roots.append(child)
        now=time.monotonic()
        if enforce_guard and (not hasattr(self,'last_health') or now-self.last_health>=0.1):
            health()
            self.last_health=now
        total = sum(rss(pid) for pid in seen)
        self.peak_rss = max(total, self.peak_rss)
        if enforce_guard and total > 128 * 1024:
            raise RuntimeError(f'fixture aggregate RSS exceeded admission: {total}KiB')
        return total

    def lock_available(self):
        path = self.root / 'resource.lock'
        assert path.stat().st_ino == self.lock_inode
        with path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            return True

    def sample(self, phase):
        self.capture()
        actors = [a for a in self.actors() if a['role'] != 'foreign' and alive(a)]
        event = {'elapsed': time.monotonic() - self.started, 'phase': phase,
                 'lock_available': self.lock_available(), 'owned_actors_alive': actors}
        self.events.append(event)
        return event

    def start(self, require_tree=True):
        health()
        assert self.sample('before-launch')['lock_available'], 'before launch lock not free'
        log = (self.root / 'wrapper.log').open('w')
        self.sentinel = subprocess.Popen([sys.executable, '-B', str(HERE / 'fixture.py'), 'foreign', str(self.root), 'wait'], stdout=log, stderr=log)
        self.proc = subprocess.Popen([sys.executable, '-B', str(HERE / 'entry.py'), str(self.module), str(self.root), self.mode, self.injection], stdout=log, stderr=log, start_new_session=True, env={'PATH': '/usr/bin:/bin', 'HOME': str(Path.home()), 'PYTHONDONTWRITEBYTECODE': '1'})
        log.close()
        if not require_tree:
            return
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            event = self.sample('start')
            if len(event['owned_actors_alive']) >= 2:
                assert not event['lock_available'], 'wrapper did not own lock while childtree active'
                return
            if self.proc.poll() is not None:
                raise RuntimeError('wrapper exited before fixture ready')
            time.sleep(0.01)
        raise RuntimeError('fixture readiness deadline')

    def unreaped_actors(self):
        return [a for a in self.actors() if a['role']!='foreign' and (current:=stat(a['pid'])) and current['starttime']==a['starttime']]

    def cancel(self, sig):
        self.proc.send_signal(sig)
        deadline = time.monotonic() + 6
        violation = None
        while time.monotonic() < deadline:
            event = self.sample('cancel')
            if event['lock_available'] and event['owned_actors_alive']:
                violation = event
                break
            if event['lock_available'] and not event['owned_actors_alive']:
                assert not self.unreaped_actors(), 'lock released before owned identities reaped'
                break
            time.sleep(0.01)
        assert self.sentinel.poll() is None, 'foreign sentinel killed'
        return violation

    def finish(self, expected_code=None):
        deadline = time.monotonic() + (8 if self.mode=='churn' else 5)
        violation = None
        while time.monotonic() < deadline:
            event = self.sample('finish')
            if event['lock_available'] and event['owned_actors_alive']:
                violation = event
                break
            code = self.proc.poll()
            if code is not None and event['lock_available'] and not event['owned_actors_alive']:
                assert not self.unreaped_actors(), 'lock released before owned identities reaped'
                if expected_code is not None:
                    assert code == expected_code, (code, expected_code)
                break
            time.sleep(0.01)
        else:
            raise RuntimeError('completion/drain deadline exceeded')
        assert self.sentinel.poll() is None, 'foreign sentinel killed'
        return violation

    def hold(self):
        (self.root / 'inject-failure').touch()
        if self.injection == 'pidfd':
            (self.root / 'spawn-extra').touch()
        self.proc.send_signal(signal.SIGTERM)
        deadline = time.monotonic() + 2.5
        seen_fault_at = None
        while time.monotonic() < deadline:
            event = self.sample('expected-HOLD')
            assert not event['lock_available'], 'cleanup error escaped guardian custody'
            assert self.sentinel.poll() is None, 'foreign sentinel killed'
            if (self.root / 'injection-observed').exists():
                if seen_fault_at is None:seen_fault_at=time.monotonic()
                if time.monotonic()-seen_fault_at>=0.3:break
            time.sleep(0.01)
        assert (self.root / 'injection-observed').exists(), 'fault injection was not reached'
        actor_ids = {a['pid'] for a in self.actors()}
        guardians = [a for pid, a in self.children.items()
                     if pid not in actor_ids and pid != self.proc.pid and alive(a)]
        assert guardians, 'no independent live guardian during incomplete cleanup'
        record=json.loads((self.root/'run/lifecycle.json').read_text())
        assert record.get('cleanup_complete') is False and record.get('lock_released') is not True and record.get('release_ready') is not True
        assert any(g['pid']==record['guardian_pid'] and (record.get('guardian_birth_ticks') is None and self.mode=='bootstrap' or g['starttime']==record.get('guardian_birth_ticks')) for g in guardians)
        assert record['lock_inode']==self.lock_inode
        if 'record' not in self.injection.split('-'):
            assert record['status']=='ownership_hold', record
        return guardians

    def kill_identity(self, actor):
        fd = None
        try:
            fd = os.pidfd_open(actor['pid'])
            if alive(actor):
                signal.pidfd_send_signal(fd, signal.SIGKILL)
        except ProcessLookupError:
            pass
        finally:
            if fd is not None:
                os.close(fd)

    def reap(self):
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    break
            except ChildProcessError:
                break

    def cleanup(self):
        def present(actor):
            current=stat(actor['pid'])
            return bool(current and current['starttime']==actor['starttime'])
        def receipt(error=None, remaining=()):
            try:
                (self.root/'test-owner-cleanup-HOLD.json').write_text(json.dumps({
                    'status':'cleanup_hold','remaining_owned':list(remaining),
                    'error':repr(error) if error else None,
                    'guardian_not_intentionally_stopped_before_workload_reaping':True},indent=2)+'\n')
            except BaseException:
                pass
        while True:
            try:
                self.capture(enforce_guard=False)
                actors=self.actors()
                self.children.update({a['pid']:a for a in actors})
                protected={p.pid for p in (self.proc,self.sentinel) if p is not None}
                if (self.module/'lifecycle.py').is_file() and self.proc is not None:
                    protected.update(pid for pid,a in self.children.items() if a.get('parent')==self.proc.pid)
                record=self.root/'run/lifecycle.json'
                try:
                    protected.add(json.loads(record.read_text())['guardian_pid'])
                except (FileNotFoundError,ValueError,KeyError):
                    pass
                registered={a['pid'] for a in actors if a['role']!='foreign'}
                workloads=[a for pid,a in self.children.items()
                           if present(a) and (pid in registered or pid not in protected)]
                for actor in workloads:
                    if alive(actor):self.kill_identity(actor)
                # Permit permanent canonical HOLD to reap once test faults are removed.
                (self.root/'inject-failure').unlink(missing_ok=True)
                self.reap()
                workloads=[a for a in workloads if present(a)]
                if workloads:
                    receipt(remaining=workloads)
                    time.sleep(0.02)
                    continue
                # Actor birth identities, including zombies, are absent before guardian termination.
                if self.unreaped_actors():
                    receipt(remaining=self.unreaped_actors())
                    time.sleep(0.02)
                    continue
                for actor in self.children.values():
                    if alive(actor):self.kill_identity(actor)
                self.reap()
                remaining=[a for a in self.children.values() if present(a)]
                if remaining or direct_children(os.getpid()):
                    receipt(remaining=remaining)
                    time.sleep(0.02)
                    continue
                break
            except BaseException as error:
                receipt(error)
                time.sleep(0.02)
        shutil.rmtree(self.tmp)
        (self.root/'events.json').write_text(json.dumps(self.events,indent=2)+'\n')
        (self.root/'cleanup.json').write_text(json.dumps({
            'survivors':[],
            'workload_birth_identities_absent_including_zombies_before_guardian_termination':True,
            'injected_fault_removed_to_allow_canonical_reaping':True,
            'sampled_aggregate_peak_rss_kib':self.peak_rss,
            'lock_available_after_cleanup':self.lock_available()},indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--module', type=Path, default=DEFAULT_MODULE)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--only-case',action='append',default=[])
    args = parser.parse_args()
    health()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        raise OSError('test subreaper setup failed')
    args.output.mkdir(exist_ok=False)
    candidate_hashes={name:hashlib.sha256((args.module/name).read_bytes()).hexdigest() for name in ('run.py','lifecycle.py') if (args.module/name).is_file()}
    script_hashes={name:hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in ('contracts.py','entry.py','fixture.py')}
    results = []
    cases = [('workload-kill', 'wait', signal.SIGKILL),
             ('workload-term', 'wait', signal.SIGTERM),
             ('workload-int', 'wait', signal.SIGINT),
             ('reentrant-cancel','reentrant-cancel',signal.SIGTERM),
             ('setup-kill', 'setup', signal.SIGKILL),
             ('setup-term', 'setup', signal.SIGTERM),
             ('setup-int', 'setup', signal.SIGINT),
             ('normal-orphan', 'normal', None),
             ('nonzero-orphan', 'nonzero', None),
             ('spawn-failure', 'spawn-failure', None),
             ('low-fd-churn', 'churn', None),
             ('foreign-output', 'foreign-output', None),
             ('source-lock', 'source-lock', None),
             ('workload-self-sigkill','workload-sigkill',None),
             ('workload-self-sigterm','workload-sigterm',None)]
    for name, mode, signum in cases:
        if args.only_case and name not in args.only_case:continue
        if args.baseline and name != 'workload-kill':
            continue
        health()
        case = Case(args.output, name, args.module, mode)
        try:
            case.start(require_tree=signum is not None)
            violation = case.cancel(signum) if signum is not None else case.finish(expected_code={'normal': 0, 'nonzero': 7, 'churn': 0, 'spawn-failure': 1,'workload-sigkill':137,'workload-sigterm':143}.get(mode))
            if signum is not None:
                code = case.proc.wait(timeout=2)
                assert code != 0, 'cancelled caller reported success'
            if args.baseline:
                assert violation, 'expected baseline premature release was not observed'
            else:
                assert violation is None, 'lock became available while owned actors live'
            if mode=='normal':
                assert str(case.root/'run/invocation.json')+'\n' in (case.root/'wrapper.log').read_text(), 'canonical stdout invocation path lost'
            if mode=='reentrant-cancel':
                assert code==143, ('reentrant cancellation exit',code)
                assert json.loads((case.root/'reentrant-observed.json').read_text())['nested_handler_invoked']
                assert 'TypeError' not in (case.root/'wrapper.log').read_text()
            if mode in ('workload-sigkill','workload-sigterm'):
                raw=-9 if mode=='workload-sigkill' else -15
                assert json.loads((case.root/'run/invocation.json').read_text())['runner_exit']==raw
                assert json.loads((case.root/'run/lifecycle.json').read_text())['exit_code']==128-raw
            if mode in ('foreign-output','source-lock'):
                assert case.proc.wait(timeout=2)!=0
                if mode=='foreign-output':assert (case.root/'run/lifecycle.json').read_text()=='FOREIGN-OUTPUT-SENTINEL'
                if mode=='source-lock':assert not (case.root/'source/forbidden.lock').exists()
            if mode == 'churn':
                assert (case.root / 'churn-complete').read_text() == '40'
                observed=[json.loads(line) for line in (case.root/'churn-observed.jsonl').read_text().splitlines()]
                assert len(observed)==40 and len({(v['pid'],v['starttime']) for v in observed})==40
            results.append({'case': name, 'expected_baseline_failure_observed': bool(violation), 'violating_sample': violation})
        finally:
            case.cleanup()
    if not args.baseline:
        for injection in ('discover', 'kill', 'reap', 'record', 'discover-stderr', 'record-stderr', 'discover-record-stderr', 'metrics', 'pidfd'):
            if args.only_case and 'hold-'+injection not in args.only_case:continue
            health()
            case = Case(args.output, 'hold-' + injection, args.module, 'wait', injection)
            try:
                case.start()
                guardians = case.hold()
                results.append({'case': 'hold-' + injection, 'independent_guardian_retained': guardians})
            finally:
                case.cleanup()
    if not args.baseline and (not args.only_case or 'hold-bootstrap-metrics' in args.only_case):
        health()
        case=Case(args.output,'hold-bootstrap-metrics',args.module,'bootstrap','metrics')
        try:
            (case.root/'inject-failure').touch()
            case.start(require_tree=False)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                event=case.sample('bootstrap-HOLD')
                if not event['lock_available'] and (case.root/'injection-observed').exists():break
                if case.proc.poll() is not None:raise RuntimeError('bootstrap fault escaped custody')
                time.sleep(0.01)
            else:raise RuntimeError('bootstrap HOLD readiness deadline')
            guardians=case.hold()
            assert not [a for a in case.actors() if a['role']!='foreign']
            results.append({'case':'hold-bootstrap-metrics','independent_guardian_retained':guardians})
        finally:
            case.cleanup()
    if args.only_case and {r['case'] for r in results}!=set(args.only_case):raise ValueError('Unknown/unexecuted selected case')
    (args.output / 'results.json').write_text(json.dumps({'baseline': args.baseline, 'module': str(args.module), 'source_sha256': hashlib.sha256((args.module / 'run.py').read_bytes()).hexdigest(), 'cases': results, 'candidate_hashes':candidate_hashes,'test_script_hashes':script_hashes,'command':sys.argv,'limits': 'External WPT/source/manifest boundaries mocked; actual canonical CLI/self-lock path. RSS sampled, not continuous peak.'}, indent=2) + '\n')
    assert candidate_hashes=={name:hashlib.sha256((args.module/name).read_bytes()).hexdigest() for name in candidate_hashes}, 'candidate changed during suite'
    print(json.dumps(results))


if __name__ == '__main__':
    main()
