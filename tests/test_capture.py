import pytest


def test_slow_window_render_is_terminated_with_bounded_wait(monkeypatch):
    from xiaomao import capture
    class Receiver:
        def poll(self,timeout):assert timeout==.25;return False
        def close(self):pass
    class Sender:
        def close(self):pass
    class Process:
        pid=123
        terminated=False
        def start(self):pass
        def join(self,timeout):pass
        def is_alive(self):return not self.terminated
        def terminate(self):self.terminated=True
        def close(self):pass
    process=Process()
    class Context:
        def RawArray(self,kind,size):return bytearray(size)
        def Pipe(self,duplex):return Receiver(),Sender()
        def Process(self,**kwargs):return process
    monkeypatch.setattr(capture.mp,'get_context',lambda kind:Context())
    with pytest.raises(capture.CaptureError):capture.capture_window(1,100,100,timeout=.25)
    assert process.terminated


def test_latest_claim_keeps_no_plaintext_and_survives_restart(tmp_path):
    from xiaomao.store import StateStore
    key=('in','text','成员','@小玲 这是不应落盘的私有正文')
    store=StateStore(tmp_path)
    assert store.claim_latest(key,100)
    assert not store.claim_latest(key,101)
    store.close();store=StateStore(tmp_path)
    assert not store.claim_latest(key,102)
    store.close()
    assert key[-1].encode() not in (tmp_path/'state.sqlite3').read_bytes()
