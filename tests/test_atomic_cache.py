from concurrent.futures import ThreadPoolExecutor
import threading


def test_concurrent_directory_and_worker_cache_writes_use_distinct_temporary_files(tmp_path,monkeypatch):
    from xiaomao import privacy
    barrier=threading.Barrier(2);paths=[]
    original=privacy.make_private
    def together(path):
        paths.append(path);original(path);barrier.wait(timeout=3)
    monkeypatch.setattr(privacy,'make_private',together)
    destination=tmp_path/'keys.dpapi'
    with ThreadPoolExecutor(2) as pool:
        tasks=[pool.submit(privacy.atomic_private_write,destination,value) for value in (b'encrypted-a',b'encrypted-b')]
        for task in tasks:task.result()
    assert len(set(paths))==2 and destination.read_bytes() in (b'encrypted-a',b'encrypted-b')
    assert list(tmp_path.iterdir())==[destination]
