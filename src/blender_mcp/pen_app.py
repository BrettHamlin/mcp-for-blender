"""LaunchServices lifetime custody. Importing this module never launches an app."""
from __future__ import annotations

import ctypes
import logging
from concurrent.futures import Future, TimeoutError
from pathlib import Path
import struct
import sys
import threading
import time


_launch_log = logging.getLogger(__name__ + '.launch')


def launch_diagnostic(phase, **fields):
    """Opt-in host timing only; never serialize app objects or protocol secrets."""
    if _launch_log.isEnabledFor(logging.DEBUG):
        record = dict(phase=phase, monotonic=time.monotonic(),
                      thread=threading.get_ident(),
                      main_thread=threading.current_thread() is threading.main_thread(), **fields)
        try:
            _launch_log.debug('pen launch %s', record, extra={'pen_launch': record})
        except Exception:
            pass  # A diagnostic sink must not break callback custody.


class LaunchTicket:
    """Retains the callback and its actual application, including late completions."""
    def __init__(self):
        self.completed = threading.Event()
        self.application = None
        self.error = None

    def complete(self, application, error):
        launch_diagnostic('callback_enter', ticket=id(self), has_application=application is not None,
                          has_error=error is not None)
        self.application = application
        self.error = str(error) if error is not None else None
        self.completed.set()
        launch_diagnostic('ticket_completed', ticket=id(self))


class DarwinApplications:
    def __init__(self, main_loop):
        if main_loop is None or not main_loop.is_running():
            raise RuntimeError("App custody requires the MCP host main asyncio loop")
        self.main_loop = main_loop
        if sys.platform != 'darwin':
            raise RuntimeError('App custody requires macOS')
        try:
            import AppKit
            import Foundation
        except ImportError as exc:
            raise RuntimeError('Install the optional pen-app PyObjC dependency before app custody') from exc
        self.appkit = AppKit
        self.foundation = Foundation

    def on_main(self, operation):
        dispatch_id = id(operation)
        launch_diagnostic('main_submit', dispatch=dispatch_id)
        def refreshed():
            if threading.current_thread() is not threading.main_thread():
                raise RuntimeError('Cocoa custody requires the host main thread')
            # NSRunningApplication's changing properties are cached until the
            # main run loop turns. NSDate.date() makes this a nonblocking turn.
            launch_diagnostic('runloop_enter', dispatch=dispatch_id)
            self.foundation.NSRunLoop.mainRunLoop().runMode_beforeDate_(
                self.foundation.NSDefaultRunLoopMode, self.foundation.NSDate.date())
            launch_diagnostic('runloop_return', dispatch=dispatch_id)
            try:
                return operation()
            finally:
                launch_diagnostic('main_operation_return', dispatch=dispatch_id)
        if threading.current_thread() is threading.main_thread():
            return refreshed()
        future = Future()
        def dispatch():
            admitted = future.set_running_or_notify_cancel()
            launch_diagnostic('main_dispatch', dispatch=dispatch_id, admitted=admitted)
            if admitted:
                try:
                    future.set_result(refreshed())
                except BaseException as exc:
                    future.set_exception(exc)
        self.main_loop.call_soon_threadsafe(dispatch)
        try:
            return future.result(timeout=1)
        except TimeoutError:
            cancelled = future.cancel()  # Skip dispatch if it has not started; never retry.
            launch_diagnostic('main_wait_timeout', dispatch=dispatch_id, cancelled=cancelled)
            raise RuntimeError('Host main run loop unavailable; custody UNKNOWN')

    def refresh(self):
        self.on_main(lambda: None)

    def launch(self, ticket, app_path, arguments):
        def launch():
            config = self.appkit.NSWorkspaceOpenConfiguration.configuration()
            config.setCreatesNewApplicationInstance_(True)
            config.setActivates_(False)
            config.setAddsToRecentItems_(False)
            config.setArguments_(arguments)
            url = self.foundation.NSURL.fileURLWithPath_(str(app_path))
            launch_diagnostic('workspace_enter', ticket=id(ticket))
            self.appkit.NSWorkspace.sharedWorkspace().openApplicationAtURL_configuration_completionHandler_(
                url, config, ticket.complete)
            launch_diagnostic('workspace_return', ticket=id(ticket))
        self.on_main(launch)

    def fresh(self, pid):
        return self.on_main(lambda: self.appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid))

    def same(self, app, fresh):
        return self.on_main(lambda: fresh is not None and bool(app.isEqual_(fresh)))

    def pid(self, app):
        return self.on_main(lambda: int(app.processIdentifier()))

    def terminated(self, app):
        return self.on_main(lambda: bool(app.isTerminated()))

    def executable(self, app):
        def executable():
            url = app.executableURL()
            if url is None:
                raise RuntimeError('Application executable identity unavailable')
            return Path(str(url.path()))
        return self.on_main(executable)

    def own_token(self):
        lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
        token = (ctypes.c_uint32 * 8)()
        count = ctypes.c_uint32(8)
        task = ctypes.c_uint32.in_dll(lib, 'mach_task_self_').value
        lib.task_info.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_void_p,
                                  ctypes.POINTER(ctypes.c_uint32)]
        lib.task_info.restype = ctypes.c_int
        if lib.task_info(task, 15, token, ctypes.byref(count)) != 0 or count.value != 8:
            raise RuntimeError('Controller TASK_AUDIT_TOKEN unavailable')
        return bytes(token).hex()

    def peer(self, connection):
        # Darwin SDK sys/un.h. LOCAL_PEERTOKEN is dynamic last_pid attribution,
        # not an immutable connection identity; callers must revalidate the app.
        pid = struct.unpack('=i', connection.getsockopt(0, 2, 4))[0]
        credential = connection.getsockopt(0, 1, 76)  # struct xucred
        version, uid = struct.unpack_from('=II', credential)
        token = connection.getsockopt(0, 6, 32)
        if version != 0 or len(token) != 32:
            raise RuntimeError('Kernel peer credentials unavailable')
        return uid, pid, token.hex()
