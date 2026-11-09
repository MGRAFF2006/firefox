#!/usr/bin/env python3
import argparse
import ctypes
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent

def identity(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        return {'pid':pid, 'birth':int(fields[19]), 'state':fields[0]}
    except (FileNotFoundError, ProcessLookupError):
        return None

def same(item):
    now = identity(item['pid'])
    return now is not None and now['birth'] == item['birth']

def stop(item):
    if not same(item):
        return
    fd = os.pidfd_open(item['pid'])
    try:
        if same(item):
            signal.pidfd_send_signal(fd, signal.SIGKILL)
    finally:
        os.close(fd)

def lock_free(path):
    with path.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX|fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

def reap():
    while True:
        try:
            pid,_=os.waitpid(-1,os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return

def executable(path, content):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(content)
    path.chmod(0o700)

def prepare(case, mode):
    root=case/'root'; source=case/'source'; binary=case/'binary/firefox'
    version='#!/bin/sh\nprintf "fixture-version\\n"\n'
    executable(binary,version)
    for path in ('clang/bin/clang','rustc/bin/rustc','rustc/bin/cargo','node/bin/node'):
        executable(root/'state'/path,version)
    executable(source/'tools/browsertime/node_modules/@sitespeed.io/geckodriver/vendor/geckodriver',version)
    p=source/'tools/browsertime/node_modules/browsertime/package.json';p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
    for name in ('package.json','moz.yaml'):
        p=source/'third_party/webkit/PerformanceTests/Speedometer3'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
    p=source/'testing/raptor/raptor/tests/benchmarks/speedometer-desktop.toml';p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# fixture\n')
    for name in ('run.py','mozconfig','smoke.cjs','build.sh','preserve.sh','summarize.py'):
        p=source/'tools/performance/baseline'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# fixture\n')
    (source/'testing/mozharness/build').mkdir(parents=True)
    executable(case/'bin/tmux','#!/bin/sh\nexit 1\n')
    executable(case/'bin/git','#!/bin/sh\nprintf "fixture-source\\n"\n')
    # Real baseline constructs its own PATH. This named fake replaces only xvfb/Raptor.
    executable(root/'state/node/bin/xvfb-run',f'#!/bin/sh\nexec {sys.executable} {HERE}/fixture.py leader\n')
    # Baseline's deliberately minimal runtime environment does not pass our test
    # variables. Give the fake external command its own explicit fixture values.
    executable(root/'state/node/bin/xvfb-run',
               f'#!/bin/sh\nFIXTURE_CASE={case} FIXTURE_EXIT={7 if mode == "nonzero" else 0} exec {sys.executable} {HERE}/fixture.py leader\n')
    return root,source

def run_case(version, mode, output, module):
    with tempfile.TemporaryDirectory(prefix='scoring-fixture-',dir=output) as directory:
        case=Path(directory);root,_=prepare(case,mode)
        records=[];process=None;sentinel=None;observations=[];guardian=None
        result=None;inode=None
        logfile=(output/f'{version}-{mode}.stdout').open('wb')
        errfile=(output/f'{version}-{mode}.stderr').open('wb')
        try:
            sentinel=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            sentinel_id=identity(sentinel.pid)
            process=subprocess.Popen([sys.executable,str(HERE/'entry.py'),str(module),str(case)],
                                     env={**os.environ,'PATH':str(case/'bin')+os.pathsep+os.environ['PATH']},
                                     stdout=logfile,stderr=errfile)
            caller=identity(process.pid)
            deadline=time.monotonic()+7
            while time.monotonic()<deadline:
                if (case/'grandchild.json').exists() and (case/'leader.json').exists():
                    break
                if process.poll() is not None:
                    raise AssertionError(f'Caller exited before workload ready: {process.returncode}')
                time.sleep(.005)
            else:
                raise AssertionError('Fixture workload readiness deadline')
            for role in ('leader','child','grandchild'):
                records.append(json.loads((case/f'{role}.json').read_text()))
            assert int((case/'creator-tid').read_text()) != records[0]['pid']
            assert records[2]['sid'] != records[0]['sid']
            inode=(root/'resource.lock').stat().st_ino
            assert not lock_free(root/'resource.lock')
            states=list(root.glob('scoring-custody/*/state.json'))
            if states:
                guardian_data=json.loads(states[0].read_text())
                guardian={'pid':guardian_data['guardian_pid'],'birth':guardian_data['guardian_birth_ticks']}
            if mode=='term':
                process.send_signal(signal.SIGTERM)
            elif mode=='kill':
                process.kill()
            else:
                (case/'release-workload').touch()
            # For killed original CLI the initial actor keeps its inherited fd
            # until it exits, but its detached close_fds descendant does not.
            if mode=='kill':
                (case/'release-workload').touch()
            unlocked_live=False
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                free=lock_free(root/'resource.lock')
                existing=[item for item in records if same(item)]
                live=[item for item in existing if identity(item['pid'])['state']!='Z']
                observations.append({'free':free,'live':live,'existing':existing,'monotonic':time.monotonic()})
                if free and live:
                    unlocked_live=True
                    break
                if free and not existing and process.poll() is not None:
                    break
                time.sleep(.002)
            else:
                raise AssertionError('Caller drain deadline; fixture retains supervision')
            process.wait(timeout=2)
            assert same(sentinel_id), 'Unrelated sentinel was stopped'
            result={'version':version,'mode':mode,'caller':caller,'actors':records,
                    'guardian':guardian,'caller_exit':process.returncode,
                    'lock_inode':inode,'unlocked_while_live':unlocked_live,
                    'sentinel_survived':True,'observations':observations}
            if version=='candidate':
                assert not unlocked_live, 'Lock released before owned drainage'
                assert not any(same(item) for item in records), 'Owned identity unreaped at release'
                state=json.loads(states[0].read_text())
                assert state['cleanup_complete'] and state['release_ready']
                assert state['owned']==[]
                result['guardian_final']=state
                logs=list(root.glob('runs/*/*/raptor.log'))
                if logs:
                    text=logs[0].read_text()
                    assert 'fixture-redaction-token' not in text
                    result['log_redaction_verified']=True
                if mode=='normal':
                    scores=list(root.glob('runs/*/*/score.json'))
                    assert len(scores)==1
                    result['fake_score']=json.loads(scores[0].read_text())
                    assert str(root/'runs') in (output/f'{version}-{mode}.stdout').read_text()
                    result['output_path_reported']=True
                assert process.returncode==({'normal':0,'nonzero':1,'term':143,'kill':-9}[mode])
            else:
                assert unlocked_live, 'Original defect was not reproduced'
            return result
        finally:
            # Stop actors before any custodian; PID/start + pidfd prevents an
            # unrelated reused numeric PID from becoming a cleanup target.
            for item in reversed(records):
                stop(item)
            if process is not None and process.poll() is None:
                process.send_signal(signal.SIGTERM)
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                if process is not None:
                    process.poll()
                reap()
                if not any(same(item) for item in records) and (guardian is None or not same(guardian)):
                    break
                time.sleep(.01)
            else:
                raise RuntimeError('Fixture custody retained: actor/guardian not gone')
            if process is not None:
                process.wait(timeout=1)
            if sentinel is not None:
                stop(sentinel_id);sentinel.wait(timeout=2)
            reap()
            assert not any(same(item) for item in records)
            assert lock_free(root/'resource.lock')
            if inode is not None:
                assert (root/'resource.lock').stat().st_ino==inode
            if result is not None:
                result['cleanup_receipt']={
                    'all_recorded_actor_births_absent':not any(same(item) for item in records),
                    'guardian_absent':guardian is None or not same(guardian),
                    'caller_waited':process is None or process.returncode is not None,
                    'sentinel_waited':sentinel is None or sentinel.returncode is not None,
                    'same_inode_lock_available':lock_free(root/'resource.lock'),
                }
            logfile.close();errfile.close()

def main():
    assert ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0)==0
    parser=argparse.ArgumentParser(description="Harmless actual-caller lock-custody contracts")
    parser.add_argument('--runner',type=Path,default=HERE.parent/'run.py')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--expect-red',action='store_true',help='Reproduce known old-caller defect')
    args=parser.parse_args()
    version='original' if args.expect_red else 'candidate'
    output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=False)

    results=[]
    try:
        for mode in ('normal','nonzero','term','kill'):
            results.append(run_case(version,mode,output,args.runner.resolve()))
            (output/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    finally:
        (output/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    print(f'{version}: {len(results)} actual caller cases complete')

if __name__=='__main__':
    main()
