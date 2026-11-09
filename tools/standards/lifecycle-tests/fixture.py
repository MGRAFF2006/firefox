import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

role, root_text, mode = sys.argv[1:4]
root = Path(root_text)
stat = Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')', 1)[1].split()
entry = {'pid': os.getpid(), 'starttime': int(stat[19]), 'role': role,
         'parent': os.getppid(), 'session': os.getsid(0)}
fd = os.open(root / 'actors.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
os.write(fd, (json.dumps(entry) + '\n').encode())
os.close(fd)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
if role == 'churn-child':
    deadline=time.monotonic()+3
    while not (root/('churn-release-'+mode)).exists() and time.monotonic()<deadline:
        time.sleep(0.005)
    sys.exit(0)
if role == 'tree':
    ready = threading.Event()
    def spawn():
        child = subprocess.Popen([sys.executable, __file__, 'leaf', root_text, mode],
                                 start_new_session=True)
        ready.set()
        child.wait()
    threading.Thread(target=spawn, daemon=True).start()
    if not ready.wait(2):
        raise RuntimeError('fixture thread did not spawn')
    deadline = time.monotonic() + 2
    while len((root / 'actors.jsonl').read_text().splitlines()) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    if mode == 'churn':
        guardian=json.loads((root/'run/lifecycle.json').read_text())['guardian_pid']
        for index in range(40):
            child=subprocess.Popen([sys.executable,__file__,'churn-child',root_text,str(index)])
            deadline=time.monotonic()+2
            seen=False
            while time.monotonic()<deadline:
                for info in Path(f'/proc/{guardian}/fdinfo').iterdir():
                    try:
                        fields=info.read_text().splitlines()
                    except FileNotFoundError:
                        continue
                    if any(line.startswith('Pid:') and line.split()[1]==str(child.pid) for line in fields):
                        child_stat=Path(f'/proc/{child.pid}/stat').read_text().rsplit(')',1)[1].split()
                        observed={'pid':child.pid,'starttime':int(child_stat[19]),'guardian_pid':guardian,'fdinfo':str(info)}
                        with (root/'churn-observed.jsonl').open('a') as stream:
                            stream.write(json.dumps(observed)+'\n')
                        seen=True
                        break
                if seen:break
                time.sleep(0.005)
            if not seen:raise RuntimeError('guardian did not pin churn child')
            (root/('churn-release-'+str(index))).touch()
            if child.wait(timeout=2)!=0:raise RuntimeError('churn child failed')
        (root / 'churn-complete').write_text('40')
    if mode in ('workload-sigkill','workload-sigterm'):
        signum=signal.SIGKILL if mode=='workload-sigkill' else signal.SIGTERM
        signal.signal(signal.SIGTERM,signal.SIG_DFL)
        os.kill(os.getpid(),signum)
    if mode in ('normal', 'nonzero', 'churn'):
        sys.exit(7 if mode == 'nonzero' else 0)
spawned_extra = False
deadline = time.monotonic() + 12
while not (root / 'release-fixtures').exists() and time.monotonic() < deadline:
    if role == 'tree' and not spawned_extra and (root / 'spawn-extra').exists():
        subprocess.Popen([sys.executable, __file__, 'extra', root_text, mode], start_new_session=True)
        spawned_extra = True
    time.sleep(0.02)
