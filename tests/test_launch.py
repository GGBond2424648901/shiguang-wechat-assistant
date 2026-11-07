def test_job_restriction_uses_local_independent_launch_instead_of_inheriting_parent(monkeypatch,tmp_path):
    from xiaomao import launch
    calls=[]
    monkeypatch.setattr(launch,'in_job',lambda:False)
    def denied(*args,**kwargs):raise PermissionError('job does not allow explicit breakaway')
    monkeypatch.setattr(launch.subprocess,'Popen',denied)
    monkeypatch.setattr(launch,'_launch_wmi',lambda arguments,root:calls.append((arguments,root)) or 123)
    with (tmp_path/'worker.log').open('ab') as output:launch.spawn_worker(tmp_path,output)
    from pathlib import Path
    assert len(calls)==1 and calls[0][0][-1]=='daemon' and calls[0][1]==Path(launch.__file__).resolve().parent.parent


def test_frozen_worker_uses_application_executable_with_explicit_data_directory(monkeypatch,tmp_path):
    from pathlib import Path
    from xiaomao import launch
    executable = tmp_path/'program'/'Xiaomao.exe'
    monkeypatch.setattr(launch.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(launch.sys, 'executable', str(executable))
    monkeypatch.setattr(launch,'in_job',lambda:True)
    calls=[]
    monkeypatch.setattr(launch,'_launch_wmi',lambda args,cwd:calls.append((args,cwd)))
    with (tmp_path/'worker.log').open('ab') as output:launch.spawn_worker(tmp_path,output,initial_mode='paused')
    assert calls==[([str(executable),'--root',str(tmp_path.resolve()),'daemon','--initial-mode','paused'],executable.parent)]


def test_process_inside_host_job_is_launched_independently_before_creating_child(monkeypatch,tmp_path):
    from xiaomao import launch
    calls=[]
    monkeypatch.setattr(launch,'in_job',lambda:True,raising=False)
    monkeypatch.setattr(launch.subprocess,'Popen',lambda *a,**k:calls.append('inherited'))
    monkeypatch.setattr(launch,'_launch_wmi',lambda *a:calls.append('independent') or 123)
    with (tmp_path/'worker.log').open('ab') as output:launch.spawn_worker(tmp_path,output)
    assert calls==['independent']


def test_explicit_initial_mode_reaches_independent_worker(monkeypatch,tmp_path):
    from xiaomao import launch
    calls=[]
    monkeypatch.setattr(launch,'in_job',lambda:True)
    monkeypatch.setattr(launch,'_launch_wmi',lambda args,root:calls.append(args) or 123)
    with (tmp_path/'worker.log').open('ab') as output:launch.spawn_worker(tmp_path,output,initial_mode='paused')
    assert calls[0][-3:]==['daemon','--initial-mode','paused']


def test_initial_mode_precedes_worker_start_and_never_bypasses_unknown():
    from types import SimpleNamespace
    from xiaomao.__main__ import apply_initial_mode
    calls=[]
    engine=SimpleNamespace(pause=lambda:calls.append('paused'),observe=lambda:calls.append('observe'),enable=lambda:calls.append('auto'))
    store=SimpleNamespace(counts=lambda:{})
    for mode in ('paused','observe','auto'):apply_initial_mode(engine,store,mode)
    assert calls==['paused','observe','auto']
    store.counts=lambda:{'unknown':1}
    apply_initial_mode(engine,store,'auto')
    assert calls==['paused','observe','auto']


def test_different_data_directory_reports_owner_without_starting_a_second_worker(monkeypatch,tmp_path):
    import os
    import uuid
    import pytest
    from xiaomao import windows
    from xiaomao.client import ControlClient, ControlUnavailable
    if os.name != 'nt':
        pytest.skip('Windows named locks')
    monkeypatch.setattr(windows, '_WORKER_MUTEX', 'Local\\XiaomaoTest-' + uuid.uuid4().hex)
    primary = tmp_path / 'primary'
    secondary = tmp_path / 'secondary'
    holder = windows.SingleInstance(primary)
    try:
        assert windows.worker_scope(primary) == 'current'
        assert windows.worker_scope(secondary) == 'other'
        monkeypatch.setattr('xiaomao.launch.spawn_worker', lambda *a, **k: pytest.fail('duplicate spawn'))
        with pytest.raises(ControlUnavailable, match='完全退出'):
            ControlClient(secondary).start(initial_mode='paused')
        assert not secondary.exists()
    finally:
        holder.close()
    assert windows.worker_scope(secondary) is None


def test_start_waits_for_same_profile_or_legacy_worker_to_publish_its_endpoint(monkeypatch,tmp_path):
    import pytest
    from xiaomao.client import ControlClient, ControlUnavailable
    client = ControlClient(tmp_path)
    for scope in ('current', 'unidentified'):
        results = iter((None, {'running': True, 'mode': 'paused'}))
        def ready():
            state = next(results)
            if state is None:
                raise ControlUnavailable('still starting')
            return state
        monkeypatch.setattr(client,'status',ready)
        monkeypatch.setattr('xiaomao.windows.worker_scope',lambda _:scope)
        monkeypatch.setattr('xiaomao.launch.spawn_worker',lambda *a,**kw:pytest.fail('duplicate spawn'))
        monkeypatch.setattr('xiaomao.client.time.sleep',lambda _:None)
        assert client.start(initial_mode='paused')['mode'] == 'paused'
