#!/usr/bin/env python3
"""Guard and launcher checks only: never invokes mach or changes the real build root."""
import argparse
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
args = parser.parse_args()
source = args.source.resolve()
runner = Path(__file__).with_name('run.py')
config = source / 'tools/performance/baseline/mozconfig'
original = config.read_bytes()
with tempfile.TemporaryDirectory(prefix='firefox-iteration-') as directory:
    root = Path(directory)
    base = [sys.executable, str(runner), '--source', str(source), '--root', str(root)]

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
    assert not list(root.iterdir()), 'Plan must not write into build root'
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
    assert config.read_bytes() == original
print('PASS: plans, closed hold, malformed/config/artifact guards, lock exclusion/release, real check exit/log/time evidence, clean stable environment, launcher failure evidence, process-group cancellation/reaping, unchanged source config')
print('No Firefox compile or browser run; toolchain version/hash paths use explicit isolated fixture executables.')
