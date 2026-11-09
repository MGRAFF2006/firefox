import importlib.util
import json
import os
from pathlib import Path
import resource
import subprocess
import sys

module_dir, root_text, mode = sys.argv[1:4]
root = Path(root_text)
sys.path.insert(0, module_dir)
spec = importlib.util.spec_from_file_location('canonical_under_test', Path(module_dir) / 'run.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.validate_source = lambda source, *args, **kwargs: None
module.validate_manifest = lambda *args, **kwargs: {'wpt_tree': 'SYNTHETIC-FIXTURE', 'selected_source_files': ['case.html']}
module.git = lambda source, *args, **kwargs: 'SYNTHETIC-FIXTURE'
module.packages = lambda *args, **kwargs: []
original_popen = subprocess.Popen
fixture = str(Path(__file__).with_name('fixture.py'))


def external_boundary(command, *args, **kwargs):
    if command and command[0] == 'sudo':
        command = [sys.executable, fixture, 'tree', root_text,
                   mode if mode in ('normal', 'nonzero', 'churn','workload-sigkill','workload-sigterm') else 'wait']
        if mode == 'spawn-failure':
            command = ['/definitely-missing-lifecycle-fixture']
    elif command and command[-1] == 'make-hosts-file':
        command = [sys.executable, '-c', 'print("127.0.0.1 web-platform.test")']
    return original_popen(command, *args, **kwargs)


subprocess.Popen = external_boundary
if mode == 'reentrant-cancel':
    import signal
    caller_pid = os.getpid()
    handlers = {}
    real_signal, real_write = signal.signal, os.write
    fired = False
    def capture_handler(signum, handler):
        if os.getpid()==caller_pid and signum in (signal.SIGINT,signal.SIGTERM) and callable(handler):
            handlers[signum]=handler
        return real_signal(signum,handler)
    def nested_write(fd, data):
        global fired
        if os.getpid()==caller_pid and data==bytes([signal.SIGTERM]) and not fired:
            fired=True
            handlers[signal.SIGINT](signal.SIGINT,None)
            (root/'reentrant-observed.json').write_text(json.dumps({'nested_handler_invoked':True,'caller_pid':caller_pid}))
        return real_write(fd,data)
    signal.signal, os.write = capture_handler, nested_write
if mode == 'churn':
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
if len(sys.argv) > 4 and sys.argv[4] != 'none':
    import lifecycle
    failure = sys.argv[4]
    parts = set(failure.split('-'))
    def observed(kind):
        with (root / 'injection-observed').open('a') as stream:
            stream.write(kind + '\n')
    if failure.endswith('-stderr'):
        class BrokenStderr:
            def write(self, value):
                raise BrokenPipeError('synthetic broken stderr')
            def flush(self):
                raise BrokenPipeError('synthetic broken stderr')
        sys.stderr = BrokenStderr()
    if 'discover' in parts:
        original = lifecycle.Supervisor.discover
        def injected(self, *args, _original=original, **kwargs):
            if (root / 'inject-failure').exists():
                observed('discover')
                raise OSError('synthetic discovery failure')
            return _original(self, *args, **kwargs)
        lifecycle.Supervisor.discover = injected
    if 'record' in parts:
        original = lifecycle.atomic_record
        def injected(*args, _original=original, **kwargs):
            if (root / 'inject-failure').exists():
                observed('record')
                raise OSError('synthetic atomic record write failure')
            return _original(*args, **kwargs)
        lifecycle.atomic_record = injected
    if failure == 'kill':
        import signal
        original = signal.pidfd_send_signal
        def injected(*args, _original=original, **kwargs):
            if (root / 'inject-failure').exists():
                observed('kill')
                raise PermissionError('synthetic owned kill failure')
            return _original(*args, **kwargs)
        signal.pidfd_send_signal = injected
    if failure == 'reap':
        original = os.waitpid
        def injected(*args, _original=original, **kwargs):
            if (root / 'inject-failure').exists():
                observed('reap')
                raise OSError('synthetic reap failure')
            return _original(*args, **kwargs)
        os.waitpid = injected
    if failure == 'metrics':
        original = Path.read_text
        def injected(self, *args, _original=original, **kwargs):
            if str(self).startswith('/proc/') and (root / 'inject-failure').exists():
                observed('metrics')
                raise OSError('synthetic ownership metric failure')
            return _original(self, *args, **kwargs)
        Path.read_text = injected

    if failure == 'pidfd':
        original = os.pidfd_open
        def injected(*args, _original=original, **kwargs):
            if (root / 'inject-failure').exists():
                observed('pidfd')
                raise OSError(24, 'synthetic pidfd fd exhaustion')
            return _original(*args, **kwargs)
        os.pidfd_open = injected

sys.argv = ['run.py', '--source', str(root / 'source'), '--python', sys.executable,
            '--binary', str(root / 'fake-binary'), '--product', 'firefox', '--label', 'SYNTHETIC-LIFECYCLE-FIXTURE',
            '--output', str(root / 'run'), '--selection', str(root / 'selection.txt'),
            '--metadata', 'mozilla', '--webdriver', str(root / 'fake-binary'), '--certutil', str(root / 'fake-binary'),
            '--manifest', str(root / 'MANIFEST.json'), '--tmp-root', (root / 'tmp-root.txt').read_text(),
            '--resource-lock', str(root / ('source/forbidden.lock' if mode=='source-lock' else 'resource.lock')), '--build-mode', 'noninstrumented-release', '--execute']
raise SystemExit(module.main())
