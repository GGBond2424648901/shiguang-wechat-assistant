from contextlib import contextmanager
import ctypes

import pytest


_PID = 2176
_CABLE = '{0.0.1.00000000}.{12341234-5678-9012-3456-789012345678}'
_OTHER_INPUT = '{0.0.1.00000000}.{87654321-4321-8765-2109-876543210987}'


class _AudioApi:
    """Model the SDK's endpoint and session out parameters without opening a stream."""

    def __init__(self, sessions, *, flow=1, state=1, actual_id=_CABLE):
        self.sessions = sessions
        self.flow = flow
        self.state = state
        self.endpoint_id = ctypes.create_unicode_buffer(actual_id)
        self.released = []
        self.freed_ids = []

    @staticmethod
    def _write(out, value):
        out._obj.value = value
        return 0

    def create_instance(self, clsid, outer, context, iid, out):
        return self._write(out, 11)

    def get_device(self, enumerator, endpoint_id, out):
        if endpoint_id != _CABLE:
            return -2147023728  # E_NOTFOUND
        return self._write(out, 12)

    def device_id(self, device, out):
        return self._write(out, ctypes.addressof(self.endpoint_id))

    def free_task_memory(self, address):
        self.freed_ids.append(address.value)

    def device_state(self, device, out):
        return self._write(out, self.state)

    def query_interface(self, interface, iid, out):
        return self._write(out, interface.value + 100)

    def data_flow(self, endpoint, out):
        return self._write(out, self.flow)

    def activate(self, device, iid, context, params, out):
        return self._write(out, 13)

    def enumerate_sessions(self, manager, out):
        return self._write(out, 14)

    def session_count(self, enumerator, out):
        return self._write(out, len(self.sessions))

    def get_session(self, enumerator, index, out):
        return self._write(out, 200 + index)

    def session_state(self, session, out):
        return self._write(out, self.sessions[session.value % 100][0])

    def session_pid(self, session, out):
        _, pid, status = self.sessions[session.value % 100]
        self._write(out, pid)
        return status

    def release(self, interface):
        self.released.append(interface.value)


@pytest.fixture
def setup_capture(monkeypatch):
    from xiaomao import windows_voice_capture as capture

    def setup(sessions=(), **device):
        api = _AudioApi(sessions, **device)

        @contextmanager
        def apartment():
            yield api

        monkeypatch.setattr(capture, '_com_apartment', apartment)
        return capture.capture_verified, api

    return setup


def test_accepts_active_capture_owned_by_bound_process(setup_capture):
    verify, api = setup_capture([(1, 42, 0), (1, _PID, 0)])

    assert verify(_PID, _CABLE) is True
    assert sorted(api.released) == [11, 12, 13, 14, 112, 200, 201, 300, 301]
    assert api.freed_ids == [ctypes.addressof(api.endpoint_id)]


@pytest.mark.parametrize('sessions', [[], [(0, _PID, 0)], [(2, _PID, 0)], [(1, 42, 0)],
                                      [(1, _PID, 0x0889000D)]])
def test_rejects_missing_inactive_foreign_or_shared_sessions(setup_capture, sessions):
    verify, _ = setup_capture(sessions)

    assert verify(_PID, _CABLE) is False


@pytest.mark.parametrize(('requested', 'settings'), [
    (_OTHER_INPUT, {}),
    (_CABLE, {'actual_id': _OTHER_INPUT}),
    (_CABLE, {'flow': 0}),
    (_CABLE, {'state': 2}),
])
def test_rejects_wrong_or_unavailable_capture_endpoint(setup_capture, requested, settings):
    verify, _ = setup_capture([(1, _PID, 0)], **settings)

    assert verify(_PID, requested) is False


@pytest.mark.parametrize(('pid', 'endpoint_id'), [(0, _CABLE), (True, _CABLE), (_PID, ''),
                                                  (_PID, _CABLE + '\0other')])
def test_rejects_invalid_capture_identity(setup_capture, pid, endpoint_id):
    verify, api = setup_capture([(1, pid, 0)])

    assert verify(pid, endpoint_id) is False
    assert not api.released


def test_fails_closed_when_com_cannot_start(monkeypatch):
    from xiaomao import windows_voice_capture as capture

    def unavailable():
        raise OSError('Core Audio unavailable')

    monkeypatch.setattr(capture, '_com_apartment', unavailable)

    assert capture.capture_verified(_PID, _CABLE) is False
