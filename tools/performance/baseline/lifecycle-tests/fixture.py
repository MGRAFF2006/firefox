#!/usr/bin/env python3
"""Harmless fake Raptor topology; no browser or network operations."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

def record(directory, role, **extra):
    fields = Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')',1)[1].split()
    data = {'pid':os.getpid(), 'birth':int(fields[19]), 'role':role,
            'pgid':os.getpgrp(), 'sid':os.getsid(0), **extra}
    path = directory/f'{role}.json'
    pending = path.with_suffix('.tmp')
    pending.write_text(json.dumps(data))
    pending.replace(path)

def main():
    directory = Path(os.environ['FIXTURE_CASE'])
    role = sys.argv[1]
    if role == 'grandchild':
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        record(directory, role)
        time.sleep(20)
    elif role == 'child':
        record(directory, role)
        subprocess.Popen([sys.executable, __file__, 'grandchild'],
                         start_new_session=True, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        # Exit immediately: the detached grandchild must be adopted and reaped.
    else:
        record(directory, 'leader')
        def spawn():
            (directory/'creator-tid').write_text(str(threading.get_native_id()))
            child = subprocess.Popen([sys.executable, __file__, 'child'],
                                     start_new_session=True, close_fds=True,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
            child.wait()
        thread = threading.Thread(target=spawn)
        thread.start()
        thread.join()
        print('fixture workload ready', flush=True)
        print("Using env: fixture-redaction-token", flush=True)
        deadline = time.monotonic()+20
        while not (directory/'release-workload').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        report = directory/'source/testing/mozharness/build/raptor.json'
        report.write_text(json.dumps({'suites':[{'name':'speedometer3-fixture', 'unit':'score', 'value':1.0}]}))
        sys.exit(int(os.environ.get('FIXTURE_EXIT', '0')))

if __name__ == '__main__':
    main()
