#!/usr/bin/env python3
"""Guard and launcher checks only: never invokes mach or changes the real build root."""
import argparse
import ctypes
import fcntl
import hashlib
import json
import os
import pwd
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--runner', type=Path, default=Path(__file__).with_name('run.py'))
parser.add_argument('--lifecycle-case', choices=('probe', 'failure', 'nested-probe', 'nested-failure', 'nested-term', 'nested-error', 'fd-churn', 'fd-recovery'))
args = parser.parse_args()
source = args.source.resolve()
runner = args.runner.resolve()
config = source / 'tools/performance/baseline/mozconfig'
original = config.read_bytes()
def assert_lock_held(root):
    with (root / 'resource.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
    raise AssertionError('Owned process is still live but resource lock was released')


def assert_gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    raise AssertionError(f'Owned process {pid} survived launcher exit')


def descriptor_regression(root, base, case):
    fixture = root / case
    fixture.mkdir()
    wrapper = fixture / 'launcher.py'
    original_wrapper = Path(base[1]).read_text()
    setup = 'import resource\nresource.setrlimit(resource.RLIMIT_NOFILE,(40,40))\n'
    if case == 'fd-recovery':
        setup += (
            'import errno,os\n'
            'cleanup=m["ProcessSupervisor"].cleanup\n'
            'discover=m["ProcessSupervisor"].discover\n'
            'def pressure_cleanup(self):\n'
            ' if self.child is not None:\n'
            '  for unused in range(24):\n'
            '   child=m["subprocess"].Popen(["/bin/true"])\n'
            '   fd=os.pidfd_open(child.pid)\n'
            '   child.wait()\n'
            '   self.owned[child.pid]=fd\n'
            '  self.inject_descriptor_error=True\n'
            ' return cleanup(self)\n'
            'def pressure_discover(self):\n'
            ' if getattr(self,"inject_descriptor_error",False):\n'
            '  self.inject_descriptor_error=False\n'
            '  raise OSError(errno.EMFILE,"injected discovery exhaustion")\n'
            ' return discover(self)\n'
            'm["ProcessSupervisor"].cleanup=pressure_cleanup\n'
            'm["ProcessSupervisor"].discover=pressure_discover\n')
    wrapper.write_text(original_wrapper.replace('try: sys.exit(m["main"]())',
                                               setup + 'try: sys.exit(m["main"]())'))
    command = ['/bin/sh', '-c',
               'i=0; while [ "$i" -lt 60 ]; do /bin/sleep .08; i=$((i+1)); done; echo finished']
    process = None
    try:
        with (fixture / 'stdout').open('w+') as stdout, (fixture / 'stderr').open('w+') as stderr:
            process = subprocess.Popen([base[0], str(wrapper), *base[2:], '--execute',
                                        'check', '--', *command], stdout=stdout,
                                       stderr=stderr, start_new_session=True)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                stderr.flush()
                stderr.seek(0)
                assert_lock_held(root)
                print(json.dumps(dict(case=case, status='timed_out_with_lock_held',
                                      stderr=stderr.read())), flush=True)
                raise
            stderr.seek(0)
            error = stderr.read()
            assert process.returncode == 0, (process.returncode, error)
            stdout.seek(0)
            output = Path(stdout.read().strip())
            evidence = json.loads((output / 'invocation.json').read_text())
            assert evidence['finished'] and evidence['exit_code'] == 0
            assert (output / 'command.log').read_text().strip() == 'finished'
            if case == 'fd-recovery':
                assert 'injected discovery exhaustion' in error
            with (root / 'resource.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print(json.dumps(dict(case=case, exit_code=0, children=60,
                                  fd_limit=40, lock_after='acquired')))
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def lifecycle_regression(root, base, case):
    # Reap fixture orphans even when checking an older, broken launcher.
    assert ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0
    fixture = root / ('lifecycle-' + case)
    fixture.mkdir()
    pid_path = fixture / 'child.pid'
    group_path = fixture / 'group.pid'
    ready_path = fixture / 'allow-exit'
    tool = root / 'state/clang/bin/clang'
    original_tool = tool.read_bytes()
    nested = case.startswith('nested-')
    mode = case.removeprefix('nested-')
    child = ('import os,pathlib,signal,time; '
             'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
             f'pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid())); '
             'time.sleep(60)')
    command = [sys.executable, '-c', 'raise RuntimeError("command must not run")']
    middle_path = fixture / 'middle.pid'
    if nested:
        leaf = 'import os; os.setsid(); ' + child
        child = ('import os,pathlib,subprocess,sys,time; '
                 f'pathlib.Path({str(middle_path)!r}).write_text(str(os.getpid())); '
                 f'subprocess.Popen([sys.executable,"-c",{leaf!r}]); '
                 'time.sleep(60)')
    if mode == 'probe' and not nested:
        tool.write_text('#!' + sys.executable + '\n' + child + '\n')
    else:
        parent = ('import os,pathlib,subprocess,sys,time\n'
                  f'pathlib.Path({str(group_path)!r}).write_text(str(os.getpgrp()))\n'
                  f'subprocess.Popen([sys.executable,"-c",{child!r}], process_group={0 if nested else None!r})\n'
                  f'while not pathlib.Path({str(ready_path)!r}).exists(): time.sleep(.01)\n'
                  'raise RuntimeError("foreground command failed before waiting for child")\n')
        if nested:
            parent = 'import signal; signal.signal(signal.SIGTERM,signal.SIG_IGN)\n' + parent
        command = [sys.executable, '-c', parent]
        if mode == 'probe':
            tool.write_text('#!' + sys.executable + '\n' + parent)
            command = [sys.executable, '-c', 'raise RuntimeError("command must not run")']
    launch_base = base
    if mode == 'error':
        wrapper = fixture / 'launcher.py'
        wrapper.write_text(
            'import runpy,sys,time,pathlib\n'
            f'm=runpy.run_path({str(runner)!r})\n'
            'output=m["ProcessSupervisor"].output\n'
            'def fixture_output(self, command, *args):\n'
            ' if command[:2] == ["git", "rev-parse"]: return b"fixture-revision\\n"\n'
            ' if command[:2] == ["git", "diff"]: return b""\n'
            ' return output(self, command, *args)\n'
            'm["ProcessSupervisor"].output=fixture_output\n'
            'sleep=time.sleep\n'
            'def fail_once(delay):\n'
            f' if pathlib.Path({str(ready_path)!r}).exists():\n'
            '  time.sleep=sleep\n'
            '  raise RuntimeError("injected supervisor failure")\n'
            ' sleep(delay)\n'
            'time.sleep=fail_once\n'
            'try: sys.exit(m["main"]())\n'
            'except (OSError,ValueError,KeyError,TypeError,m["subprocess"].CalledProcessError) as error:\n'
            ' print(f"iteration: {error}",file=sys.stderr); sys.exit(1)\n')
        launch_base = [base[0], str(wrapper), *base[2:]]
    sentinel = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    process = None
    try:
        with (fixture / 'stdout').open('w+') as stdout, (fixture / 'stderr').open('w+') as stderr:
            process = subprocess.Popen([*launch_base, '--execute', 'check', '--', *command],
                                       stdout=stdout, stderr=stderr, start_new_session=True)
            deadline = time.monotonic() + 10
            while not pid_path.exists():
                assert process.poll() is None, 'Launcher exited before fixture child started'
                assert time.monotonic() < deadline, 'Fixture child failed to start'
                time.sleep(.01)
            pid = int(pid_path.read_text())
            assert_lock_held(root)
            before = Path(f'/proc/{pid}/stat').read_text()
            print(json.dumps(dict(case=case, stage='before', leaf_stat=before.strip(),
                                  middle_stat=Path(f'/proc/{int(middle_path.read_text())}/stat').read_text().strip() if middle_path.exists() else None,
                                  lock='busy', sentinel_pid=sentinel.pid)), flush=True)
            if mode in ('probe', 'term'):
                process.send_signal(signal.SIGTERM)
                time.sleep(.1)
                assert process.poll() is None, 'Probe cancellation exited before stubborn probe cleanup'
                assert_lock_held(root)
            else:
                ready_path.touch()
            samples = 0
            while process.poll() is None:
                with (root / 'resource.lock').open('a') as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        samples += 1
                    else:
                        print(json.dumps(dict(case=case, stage='lock-acquired',
                                              leaf_exists=Path(f'/proc/{pid}').exists())), flush=True)
                        assert_gone(pid)
                assert time.monotonic() < deadline + 5
                time.sleep(.005)
            process.wait(timeout=10)
            assert sentinel.poll() is None, 'Launcher signalled unrelated sibling sentinel'
            expected = 143 if mode in ('probe', 'term') else 1
            assert process.returncode == expected, (case, process.returncode)
            assert_gone(pid)
            if middle_path.exists():
                assert_gone(int(middle_path.read_text()))
            stdout.seek(0)
            evidence = json.loads((Path(stdout.read().strip()) / 'invocation.json').read_text())
            assert evidence['finished'] and evidence['exit_code'] == expected
            if mode in ('probe', 'term'):
                assert evidence['status'] == 'cancelled'
                assert evidence['cancelled_signal'] == signal.SIGTERM
                assert evidence['executed'] is (mode != 'probe')
                if mode == 'probe':
                    assert evidence['active_command'] == [str(tool), '--version']
            else:
                assert evidence['status'] == ('launcher_error' if mode == 'error' else 'completed')
                assert evidence['executed'] is True
            with (root / 'resource.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print(json.dumps(dict(case=case, pid=pid, before_stat=before.strip(),
                                  lock_busy_samples=samples, exit_code=process.returncode,
                                  status=evidence['status'], owned_after='gone',
                                  sentinel='alive', lock_after='acquired')))
    finally:
        sentinel.kill()
        sentinel.wait()
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        if group_path.exists():
            try:
                os.killpg(int(group_path.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
        if middle_path.exists():
            try:
                os.kill(int(middle_path.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
        if pid_path.exists():
            pid = int(pid_path.read_text())
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
        while True:
            try:
                os.waitpid(-1, 0)
            except ChildProcessError:
                break
        tool.write_bytes(original_tool)


with tempfile.TemporaryDirectory(prefix='firefox-iteration-') as directory:
    root = Path(directory)
    fixture_runner = root / 'runner.py'
    fixture_runner.write_text(
        'import runpy,sys\n'
        f'm=runpy.run_path({str(runner)!r})\n'
        'output=m["ProcessSupervisor"].output\n'
        'def fixture_output(self, command, *args):\n'
        ' if command[:2] == ["git", "rev-parse"]: return b"fixture-revision\\n"\n'
        ' if command[:2] == ["git", "diff"]: return b""\n'
        ' return output(self, command, *args)\n'
        'm["ProcessSupervisor"].output=fixture_output\n'
        'try: sys.exit(m["main"]())\n'
        'except (OSError,ValueError,KeyError,TypeError,m["subprocess"].CalledProcessError) as error:\n'
        ' print(f"iteration: {error}",file=sys.stderr); sys.exit(1)\n')
    base = [sys.executable, str(fixture_runner), '--source', str(source), '--root', str(root)]

    def call(*options, success=True, message=None, env=None):
        run = subprocess.run([*base, *options], capture_output=True, text=True, env=env)
        assert (run.returncode == 0) == success, (run.returncode, run.stdout, run.stderr)
        if message:
            assert message in run.stderr, run.stderr
        return run

    for target, suffix in [('binaries', ['binaries']), ('faster', ['faster']), ('full', [])]:
        plan = json.loads(call(target).stdout)
        assert plan['command'] == ['./mach', '--no-interactive', 'build', *suffix]
        assert plan['executed'] is False and plan['readiness'].startswith('blocked:')
    assert list(root.iterdir()) == [fixture_runner], 'Plan must not write into build root'
    call('--execute', 'binaries', success=False, message='baseline-complete.json')
    marker = root / 'baseline-complete.json'
    marker.write_text('[]')
    call('--execute', 'binaries', success=False, message='schema')
    marker.write_text('{')
    call('--execute', 'binaries', success=False, message='iteration:')
    artifact = root / 'fixture-evidence'
    artifact.write_text('Fixture only; not baseline evidence\n')
    artifact.chmod(0o700)
    data = dict(schema=1, source_revision='6181e6b5f28a8fbae1d5c3ef6f6626b5a2180213',
                mozconfig_sha256='wrong', preserved_runtime=str(artifact),
                baseline_evidence=str(artifact), profile_evidence=str(artifact))
    marker.write_text(json.dumps(data))
    call('--execute', 'binaries', success=False, message='hash mismatch')
    data['mozconfig_sha256'] = hashlib.sha256(original).hexdigest()
    data['profile_evidence'] = str(root / 'absent')
    marker.write_text(json.dumps(data))
    call('--execute', 'binaries', success=False, message='profile_evidence')
    data['profile_evidence'] = str(artifact)
    marker.write_text(json.dumps(data))
    with (root / 'resource.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        call('--execute', 'binaries', success=False, message='Resource busy')
    call('--execute', 'binaries', success=False, message='Unset build environment', env=dict(os.environ, CC='/unapproved/compiler'))
    call('--execute', 'binaries', success=False, message='Warm configured')
    (root / 'obj-release').mkdir()
    (root / 'obj-release/config.status').touch()
    info = dict(topsrcdir=str(source), topobjdir=str(root / 'obj-release'), mozconfig=str(config))
    info_path = root / 'obj-release/mozinfo.json'
    info_path.write_text(json.dumps(dict(info, topsrcdir='/another-checkout')))
    call('--execute', 'binaries', success=False, message='paths mismatch')
    info_path.write_text(json.dumps(info))
    for folder, names in [('rustc', ['cargo', 'rustc']), ('clang', ['clang'])]:
        directory = root / 'state' / folder / 'bin'
        directory.mkdir(parents=True)
        for name in names:
            tool = directory / name
            tool.write_text('#!/bin/sh\nprintf "fixture toolchain only; PATH=%s; marker=%s\\n" "$PATH" "${ITERATION_SECRET-absent}"\n')
            tool.chmod(0o700)
    (root / 'tmp').mkdir()
    if args.lifecycle_case:
        if args.lifecycle_case.startswith('fd-'):
            descriptor_regression(root, base, args.lifecycle_case)
        else:
            lifecycle_regression(root, base, args.lifecycle_case)
        print('PASS lifecycle regression:', args.lifecycle_case)
        sys.exit(0)
    probe = ('import fcntl,os,pathlib; '
             'r=pathlib.Path(os.environ["FIREFOX_BASELINE_ROOT"]); '
             'f=(r/"resource.lock").open("a"); '
             '\ntry: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
             '\nexcept BlockingIOError: print("lock held; command environment:",os.environ["MOZCONFIG"])'
             '\nelse: raise RuntimeError("lock not held")')
    run = call('--execute', 'check', '--', sys.executable, '-c', probe)
    output = Path(run.stdout.strip())
    evidence = json.loads((output / 'invocation.json').read_text())
    assert evidence['exit_code'] == 0 and evidence['mozconfig_preserved']
    assert 'lock held' in (output / 'command.log').read_text()
    assert evidence['elapsed_seconds'] > 0 and evidence['resources']['max_child_rss_kib_including_toolchain_probes'] > 0
    run = call('--execute', 'check', '--', sys.executable, '-c', 'import sys; sys.exit(7)', success=False)
    assert run.returncode == 7
    assert json.loads((Path(run.stdout.strip()) / 'invocation.json').read_text())['exit_code'] == 7
    environment_probe = (
        'import os,pwd; '
        'assert os.environ["PATH"] == "/usr/local/bin:/usr/bin:/bin"; '
        'assert os.environ["HOME"] == pwd.getpwuid(os.getuid()).pw_dir; '
        'assert os.environ["LANG"] == "C.UTF-8"; '
        'assert os.environ["LC_ALL"] == "C.UTF-8"; '
        'assert os.environ["TZ"] == "Europe/Berlin"; '
        'assert "ITERATION_SECRET" not in os.environ')
    ambient = dict(os.environ, PATH='/usr/bin:/bin:/unapproved',
                   HOME='/unapproved/home', ITERATION_SECRET='fixture-secret',
                   LANG='C', TZ='UTC')
    run = call('--execute', 'check', '--', sys.executable, '-c', environment_probe, env=ambient)
    clean_evidence = json.loads((Path(run.stdout.strip()) / 'invocation.json').read_text())
    assert clean_evidence['environment']['PATH'] == '/usr/local/bin:/usr/bin:/bin'
    assert 'fixture-secret' not in json.dumps(clean_evidence)
    assert len(clean_evidence['build_inputs_sha256']) == 64
    for tool in clean_evidence['toolchain'].values():
        assert 'PATH=/usr/local/bin:/usr/bin:/bin; marker=absent' in tool['version']
    run = call('--execute', 'check', '--', str(root / 'absent-command'), success=False)
    failure = json.loads((Path(run.stdout.strip()) / 'invocation.json').read_text())
    assert failure['status'] == 'launcher_error' and failure['launcher_error'] == 'FileNotFoundError'
    descendant = ('import os,pathlib,signal,time; '
                  'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                  'pathlib.Path(os.environ["FIREFOX_BASELINE_ROOT"],"descendant.pid").write_text(str(os.getpid())); '
                  'time.sleep(60)')
    parent = ('import subprocess,signal,time,sys; '
              'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
              'subprocess.Popen([sys.executable,"-c",' + repr(descendant) + ']); '
              'time.sleep(60)')
    process = subprocess.Popen([*base, '--execute', 'check', '--', sys.executable, '-c', parent],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    pid_path = root / 'descendant.pid'
    deadline = time.monotonic() + 10
    try:
        while not pid_path.exists():
            assert process.poll() is None, 'Launcher exited before descendant started'
            assert time.monotonic() < deadline, 'Descendant failed to start'
            time.sleep(0.02)
        descendant_pid = int(pid_path.read_text())
        with (root / 'resource.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise AssertionError('Live command must retain lock')
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 143, (process.returncode, stderr)
        cancelled = json.loads((Path(stdout.strip()) / 'invocation.json').read_text())
        assert cancelled['status'] == 'cancelled' and cancelled['cancelled_signal'] == signal.SIGTERM
        assert cancelled['exit_code'] == 143 and cancelled['finished']
        try:
            os.kill(descendant_pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError('Cancelled grandchild was not killed and reaped')
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=10)
    with (root / 'resource.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    lifecycle_regression(root, base, 'probe')
    lifecycle_regression(root, base, 'failure')
    for case in ('nested-probe', 'nested-failure', 'nested-term', 'nested-error'):
        lifecycle_regression(root, base, case)
    for case in ('fd-churn', 'fd-recovery'):
        descriptor_regression(root, base, case)
    assert config.read_bytes() == original
print('PASS: plans, closed hold, malformed/config/artifact guards, lock exclusion/release, real check exit/log/time evidence, clean stable environment, launcher failure evidence, process-group cancellation/reaping, blocked-probe cancellation, failed-command descendant cleanup, unchanged source config')
print('No Firefox compile or browser run; toolchain version/hash paths use explicit isolated fixture executables.')
