"""Keep the WPT resource lock in a caller-independent process custodian."""
import ctypes
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import tempfile
import time


def atomic_record(path, data):
    pending = path.with_name(path.name + '.tmp')
    with pending.open('w') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)


def process_exited(fd):
    poll = select.poll()
    poll.register(fd, select.POLLIN)
    events = poll.poll(0)
    if events and events[0][1] & (select.POLLERR | select.POLLNVAL):
        raise OSError('Cannot establish pidfd process state')
    return bool(events and events[0][1] & (select.POLLIN | select.POLLHUP))


class Cancelled(Exception):
    pass


class Supervisor:
    def __init__(self, owner_pipe, lock, record_path):
        self.owner_pipe = owner_pipe
        self.lock = lock
        self.record_path = record_path
        self.child = None
        self.owned = {}
        self.cancelled = None
        self.state = {'guardian_pid': os.getpid(), 'status': 'preparing',
                      'cleanup_complete': False, 'lock_released': False,
                      'lock_path': str(Path(lock.name).resolve())}

    def cancel(self, signum, frame):
        if self.cancelled is None:
            self.cancelled = signum

    def check_cancelled(self):
        if self.owner_pipe is not None:
            poll = select.poll()
            poll.register(self.owner_pipe, select.POLLIN | select.POLLHUP)
            if poll.poll(0):
                message = os.read(self.owner_pipe, 64)
                self.state['caller_disconnected'] = True
                self.cancelled = self.cancelled or (message[0] if message else signal.SIGTERM)
                os.close(self.owner_pipe)
                self.owner_pipe = None
        if self.cancelled is not None:
            raise Cancelled()

    def write_record(self, **fields):
        self.state.update(fields)
        self.state['owned'] = [{'pid': pid, 'birth_ticks': birth}
                               for pid, (_, birth) in sorted(self.owned.items())]
        try:
            atomic_record(self.record_path, self.state)
        except BaseException as error:
            self.hold(error)

    def write_evidence(self, path, evidence):
        try:
            atomic_record(path, evidence)
        except BaseException as error:
            self.hold(error)

    def retire_exited(self):
        for pid, (fd, _) in list(self.owned.items()):
            if not process_exited(fd):
                continue
            if self.child is not None and pid == self.child.pid:
                self.child.poll()
            else:
                try:
                    os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    pass
            os.close(fd)
            del self.owned[pid]

    def discover(self):
        self.retire_exited()
        parents = [os.getpid()]
        seen = set()
        while parents:
            parent = parents.pop()
            if parent in seen:
                continue
            seen.add(parent)
            if parent != os.getpid() and process_exited(self.owned[parent][0]):
                continue
            try:
                tasks = list(Path(f'/proc/{parent}/task').iterdir())
            except (FileNotFoundError, ProcessLookupError):
                if parent != os.getpid() and process_exited(self.owned[parent][0]):
                    continue
                raise
            for task in tasks:
                try:
                    children = (task / 'children').read_text().split()
                except (FileNotFoundError, ProcessLookupError):
                    try:
                        task.stat()
                    except (FileNotFoundError, ProcessLookupError):
                        continue
                    if parent != os.getpid() and process_exited(self.owned[parent][0]):
                        continue
                    raise
                for value in children:
                    pid = int(value)
                    if pid not in self.owned:
                        fd = None
                        try:
                            fd = os.pidfd_open(pid)
                            stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                        except OSError as error:
                            if fd is not None:
                                os.close(fd)
                            if isinstance(error, (FileNotFoundError, ProcessLookupError)):
                                continue
                            raise
                        valid = int(stat[1]) == parent and not process_exited(fd)
                        if parent != os.getpid():
                            valid = valid and not process_exited(self.owned[parent][0])
                        if not valid:
                            os.close(fd)
                            continue
                        self.owned[pid] = (fd, int(stat[19]))
                    parents.append(pid)

    def signal_owned(self, signum):
        error = None
        try:
            self.discover()
        except BaseException as failure:
            error = failure
        for fd, _ in self.owned.values():
            try:
                signal.pidfd_send_signal(fd, signum)
            except ProcessLookupError:
                pass
            except BaseException as failure:
                error = error or failure
        if error is not None:
            raise error

    def drain_once(self):
        self.signal_owned(signal.SIGKILL)
        if self.child is not None:
            self.child.poll()
        children = False
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                children = True
                break
        self.retire_exited()
        if not children and not self.owned:
            self.child = None
            return True
        return False

    def cleanup(self):
        self.write_record(status='draining', cleanup_complete=False)
        while True:
            if self.drain_once():
                return
            self.write_record(status='draining', cleanup_complete=False)
            time.sleep(0.02)

    def hold(self, error):
        # Retry owned drainage, but uncertainty permanently retains lock custody.
        self.state.update(status='ownership_hold', cleanup_complete=False,
                          lock_released=False, failure_type=type(error).__name__,
                          failure=str(error))
        while True:
            self.state['owned'] = [{'pid': pid, 'birth_ticks': birth}
                                   for pid, (_, birth) in sorted(self.owned.items())]
            try:
                atomic_record(self.record_path, self.state)
            except BaseException:
                pass
            try:
                self.drain_once()
            except BaseException:
                pass
            time.sleep(0.1)

    def run(self, command, cwd, env, log):
        self.check_cancelled()
        self.write_record(status='running', active_command=command)
        try:
            self.child = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                          stdout=log, stderr=subprocess.STDOUT,
                                          start_new_session=True)
            deadline = None
            while self.child.poll() is None:
                self.discover()
                try:
                    self.check_cancelled()
                except Cancelled:
                    if deadline is None:
                        self.signal_owned(self.cancelled)
                        deadline = time.monotonic() + 1
                    elif time.monotonic() >= deadline:
                        self.signal_owned(signal.SIGKILL)
                time.sleep(0.02)
            code = self.child.returncode
        except BaseException as error:
            if self.child is not None:
                self.hold(error)
            raise
        finally:
            try:
                self.cleanup()
            except BaseException as error:
                self.hold(error)
        self.check_cancelled()
        return code

    def output(self, command, cwd, env):
        with tempfile.TemporaryFile() as log:
            code = self.run(command, cwd, env, log)
            try:
                log.seek(0)
                output = log.read()
            except BaseException as error:
                self.hold(error)
        if code:
            raise subprocess.CalledProcessError(code, command, output=output)
        return output


def _guardian(owner_pipe, lock_path, record_path, callback, caller_pid):
    os.setsid()
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise ValueError('Linux Python pidfd APIs are required')
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'Cannot enable child subreaper')
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # A failed directory claim must not write into another invocation's output.
        record_path.parent.mkdir(parents=True, exist_ok=False)
        supervisor = Supervisor(owner_pipe, lock, record_path)
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, supervisor.cancel)
        try:
            lock_stat = os.fstat(lock.fileno())
            supervisor.state.update(lock_device=lock_stat.st_dev, lock_inode=lock_stat.st_ino,
                                    caller_pid=caller_pid)
            supervisor.state['guardian_birth_ticks'] = int(
                Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')', 1)[1].split()[19])
            supervisor.write_record()
        except BaseException as error:
            supervisor.hold(error)
        try:
            supervisor.check_cancelled()
            code = callback(supervisor)
        except Cancelled:
            code = 128 + (supervisor.cancelled or signal.SIGTERM)
        except BaseException as error:
            try:
                supervisor.cleanup()
                supervisor.write_record(status='failed', failure_type=type(error).__name__,
                                        failure=str(error))
            except BaseException as cleanup_error:
                supervisor.hold(cleanup_error)
            code = error.code if isinstance(error, SystemExit) and isinstance(error.code, int) else 1
        try:
            supervisor.cleanup()
            try:
                supervisor.check_cancelled()
            except Cancelled:
                code = 128 + (supervisor.cancelled or signal.SIGTERM)
            if code < 0:
                code = 128 - code
            supervisor.write_record(status='cancelled' if supervisor.cancelled else
                                    ('completed' if code == 0 else 'failed'),
                                    cleanup_complete=True, release_ready=True,
                                    cancelled_signal=supervisor.cancelled, exit_code=code)
        except BaseException as error:
            supervisor.hold(error)
        return code


def run_guarded(lock_path, record_path, callback):
    caller_pid = os.getpid()
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    pid = os.fork()
    if pid == 0:
        os.close(write_fd)
        try:
            code = _guardian(read_fd, lock_path, record_path, callback, caller_pid)
        except BaseException as error:
            try:
                os.write(2, f'WPT guardian startup failed: {type(error).__name__}: {error}\n'.encode())
            except BaseException:
                pass
            code = 1
        os._exit(code if 0 <= code <= 255 else 1)
    os.close(read_fd)
    owner_fd = write_fd

    def cancel(signum, frame):
        nonlocal owner_fd
        fd, owner_fd = owner_fd, None
        if fd is not None:
            try:
                os.write(fd, bytes([signum]))
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass

    previous = {signum: signal.signal(signum, cancel) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        _, status = os.waitpid(pid, 0)
        code = os.waitstatus_to_exitcode(status)
        return code if code >= 0 else 128 - code
    finally:
        fd, owner_fd = owner_fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        for signum, handler in previous.items():
            signal.signal(signum, handler)
