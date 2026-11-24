import hashlib
from io import BytesIO
import zipfile

import httpx
import pytest

from xiaomao import voice_driver


def archive(files):
    output = BytesIO()
    with zipfile.ZipFile(output, 'w') as bundle:
        for name, contents in files.items():
            bundle.writestr(name, contents)
    return output.getvalue()


@pytest.fixture
def package(monkeypatch):
    files = {
        'VBCABLE_Setup_x64.exe': b'vendor installer',
        'vbMmeCable64_win10.inf': b'vendor driver manifest',
        'readme.txt': b'attribution',
    }
    payload = archive(files)

    def pin(data):
        monkeypatch.setattr(voice_driver, 'PACKAGE_SIZE', len(data))
        monkeypatch.setattr(voice_driver, 'PACKAGE_SHA256', hashlib.sha256(data).hexdigest())

    pin(payload)
    return files, payload, pin


def test_driver_download_checks_bytes_caches_and_extracts_the_original_installer(tmp_path, package):
    files, payload, _ = package
    requests = []
    progress = []

    def serve(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=payload)

    cache = tmp_path / 'package-cache'
    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        with voice_driver.prepare_installer(cache, client=client,
                                            progress=lambda *counts: progress.append(counts)) as installer:
            assert installer.read_bytes() == files[installer.name]
            assert (installer.parent / 'vbMmeCable64_win10.inf').read_bytes() == files['vbMmeCable64_win10.inf']
            assert (installer.parent / 'readme.txt').read_bytes() == files['readme.txt']
        assert not installer.exists()
        with voice_driver.prepare_installer(cache, client=client) as another:
            assert another.read_bytes() == files[another.name]
    assert requests == [voice_driver.PACKAGE_URL]
    assert progress[-1] == (len(payload), len(payload))


@pytest.mark.parametrize('variant', ('wrong-bytes', 'larger', 'shorter', 'redirect'))
def test_unverified_driver_is_never_cached_or_made_available(tmp_path, package, variant):
    _, payload, _ = package
    content = {
        'wrong-bytes': b'0' * len(payload), 'larger': payload + b'x', 'shorter': payload[:-1],
    }.get(variant, b'')
    response = httpx.Response(302, headers={'Location': 'https://other.example/install.zip'}) if variant == 'redirect' else (
        httpx.Response(200, content=content)
    )
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as client:
        with pytest.raises(ValueError, match='VB-CABLE'):
            with voice_driver.prepare_installer(tmp_path, client=client):
                pytest.fail('unverified installer was exposed')
    assert not list(tmp_path.iterdir())


def test_installer_package_cannot_write_outside_its_temporary_directory(tmp_path, package):
    files, _, pin = package
    contents = archive(files | {'../escape.exe': b'unsafe'})
    pin(contents)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=contents))) as client:
        with pytest.raises(ValueError, match='VB-CABLE'):
            with voice_driver.prepare_installer(tmp_path, client=client):
                pytest.fail('unexpected package entries were accepted')
    assert not (tmp_path / 'escape.exe').exists()
