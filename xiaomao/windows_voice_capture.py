"""Verify an existing Windows capture session without opening the microphone."""

from contextlib import ExitStack, contextmanager
import ctypes
from functools import lru_cache
import os
from uuid import UUID


_PTR = ctypes.c_void_p
_DWORD = ctypes.c_uint32
_INT = ctypes.c_int32
_OUT_PTR = ctypes.POINTER(_PTR)
_OUT_DWORD = ctypes.POINTER(_DWORD)
_OUT_INT = ctypes.POINTER(_INT)


class _Guid(ctypes.Structure):
    _fields_ = [
        ('data1', _DWORD), ('data2', ctypes.c_uint16), ('data3', ctypes.c_uint16),
        ('data4', ctypes.c_uint8 * 8),
    ]


def _guid(value):
    return ctypes.byref(_Guid.from_buffer_copy(UUID(value).bytes_le))


_MM_DEVICE_ENUMERATOR = _guid('BCDE0395-E52F-467C-8E3D-C4579291692E')
_IID_DEVICE_ENUMERATOR = _guid('A95664D2-9614-4F35-A746-DE8DB63617E6')
_IID_ENDPOINT = _guid('1BE09788-6894-4089-8586-9A2A6C265AC5')
_IID_SESSION_MANAGER = _guid('77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F')
_IID_SESSION_CONTROL = _guid('BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D')
_GUID_PTR = ctypes.POINTER(_Guid)

_CLSCTX_ALL = 23
_RPC_E_CHANGED_MODE = ctypes.c_int32(0x80010106).value
_DEVICE_STATE_ACTIVE = 1
_E_CAPTURE = 1
_AUDIO_SESSION_ACTIVE = 1


@lru_cache(maxsize=1)
def _core_audio():
    if os.name != 'nt':
        raise OSError('Windows Core Audio is unavailable')
    api = ctypes.WinDLL('ole32.dll')
    api.initialize = api.CoInitializeEx
    api.initialize.argtypes = [_PTR, _DWORD]
    api.initialize.restype = _INT
    api.uninitialize = api.CoUninitialize
    api.uninitialize.argtypes = []
    api.uninitialize.restype = None
    api.create_instance = api.CoCreateInstance
    api.create_instance.argtypes = [_GUID_PTR, _PTR, _DWORD, _GUID_PTR, _OUT_PTR]
    api.create_instance.restype = _INT
    api.free_task_memory = api.CoTaskMemFree
    api.free_task_memory.argtypes = [_PTR]
    api.free_task_memory.restype = None

    def method(slot, name, *arguments, result=_INT):
        return ctypes.WINFUNCTYPE(result, *arguments)(slot, name)

    # Slots include inherited methods. See mmdeviceapi.h and audiopolicy.h.
    api.query_interface = method(0, 'IUnknown::QueryInterface', _GUID_PTR, _OUT_PTR)
    api.release = method(2, 'IUnknown::Release', result=_DWORD)
    api.get_device = method(5, 'IMMDeviceEnumerator::GetDevice', ctypes.c_wchar_p, _OUT_PTR)
    api.activate = method(3, 'IMMDevice::Activate', _GUID_PTR, _DWORD, _PTR, _OUT_PTR)
    api.device_id = method(5, 'IMMDevice::GetId', _OUT_PTR)
    api.device_state = method(6, 'IMMDevice::GetState', _OUT_DWORD)
    api.data_flow = method(3, 'IMMEndpoint::GetDataFlow', _OUT_INT)
    api.enumerate_sessions = method(5, 'IAudioSessionManager2::GetSessionEnumerator', _OUT_PTR)
    api.session_count = method(3, 'IAudioSessionEnumerator::GetCount', _OUT_INT)
    api.get_session = method(4, 'IAudioSessionEnumerator::GetSession', _INT, _OUT_PTR)
    api.session_state = method(3, 'IAudioSessionControl::GetState', _OUT_INT)
    api.session_pid = method(14, 'IAudioSessionControl2::GetProcessId', _OUT_DWORD)
    return api


@contextmanager
def _com_apartment():
    api = _core_audio()
    status = api.initialize(None, 0)  # COINIT_MULTITHREADED
    if status not in (0, 1, _RPC_E_CHANGED_MODE):
        raise OSError(f'Core Audio COM initialization failed ({status:#x})')
    try:
        yield api
    finally:
        if status != _RPC_E_CHANGED_MODE:
            api.uninitialize()


def _read_out(method, value_type, *arguments):
    value = value_type()
    status = method(*arguments, ctypes.byref(value))
    if status != 0:
        raise OSError(f'Core Audio returned {status:#x}')
    return value


def _interface(stack, api, method, *arguments):
    pointer = _read_out(method, _PTR, *arguments)
    if not pointer:
        raise OSError('Core Audio did not return an interface')
    stack.callback(api.release, pointer)
    return pointer


def _capture_endpoint_matches(stack, api, device, endpoint_id):
    address = _read_out(api.device_id, _PTR, device)
    if not address:
        return False
    try:
        if ctypes.wstring_at(address) != endpoint_id:
            return False
    finally:
        api.free_task_memory(address)

    if _read_out(api.device_state, _DWORD, device).value != _DEVICE_STATE_ACTIVE:
        return False
    endpoint = _interface(stack, api, api.query_interface, device, _IID_ENDPOINT)
    return _read_out(api.data_flow, _INT, endpoint).value == _E_CAPTURE


def _active_session_matches(api, sessions, index, pid):
    with ExitStack() as stack:
        session = _interface(stack, api, api.get_session, sessions, index)
        owner = _interface(stack, api, api.query_interface, session, _IID_SESSION_CONTROL)
        session_pid = _DWORD()
        # A positive success code can identify only the creator of a shared session.
        if api.session_pid(owner, ctypes.byref(session_pid)) != 0 or session_pid.value != pid:
            return False
        return _read_out(api.session_state, _INT, session).value == _AUDIO_SESSION_ACTIVE


def capture_verified(pid: int, input_endpoint_id: str) -> bool:
    """Check that the verified WeChat window PID has active capture on the selected endpoint."""
    if (type(pid) is not int or not 0 < pid <= 0xFFFFFFFF or
            not isinstance(input_endpoint_id, str) or not input_endpoint_id or
            not input_endpoint_id.isprintable()):
        return False
    try:
        with _com_apartment() as api, ExitStack() as stack:
            enumerator = _interface(stack, api, api.create_instance, _MM_DEVICE_ENUMERATOR,
                                    None, _CLSCTX_ALL, _IID_DEVICE_ENUMERATOR)
            device = _interface(stack, api, api.get_device, enumerator, input_endpoint_id)
            if not _capture_endpoint_matches(stack, api, device, input_endpoint_id):
                return False
            manager = _interface(stack, api, api.activate, device, _IID_SESSION_MANAGER, _CLSCTX_ALL, None)
            sessions = _interface(stack, api, api.enumerate_sessions, manager)
            count = _read_out(api.session_count, _INT, sessions).value
            return any(_active_session_matches(api, sessions, index, pid) for index in range(count))
    except OSError:
        return False
