import json
import os
from pathlib import Path
import subprocess
import sys
import pytest


def run_cli(root, command):
    result = subprocess.run([sys.executable,'-m','xiaomao','--root',str(root),command],
                            cwd=Path(__file__).resolve().parent.parent,capture_output=True,
                            encoding='utf-8',env={**os.environ,'PYTHONIOENCODING':'utf-8'},timeout=25)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_doctor_reports_configuration_without_revealing_ciphertext(tmp_path):
    result=run_cli(tmp_path,'doctor')
    assert result['credential_encrypted'] is False
    assert result['calibrated'] is False
    assert 'api_key' not in result


def test_stdin_key_command_accepts_backup_lines_without_echo(tmp_path):
    from xiaomao.credentials import CredentialStore
    keys=['sk-test-cli-primary','sk-test-cli-backup']
    proc=subprocess.run([sys.executable,'-m','xiaomao','--root',str(tmp_path),'key','--stdin'],
                        input='\n'.join(keys)+'\n',text=True,encoding='utf-8',capture_output=True,timeout=25)
    assert proc.returncode==0
    assert all(key not in proc.stdout+proc.stderr for key in keys)
    assert CredentialStore(tmp_path/'runtime').get_keys()==tuple(keys)


def test_cli_worker_lifecycle_is_observation_only_and_cleans_endpoint(tmp_path):
    from xiaomao.windows import SingleInstance, DesktopUnavailable
    try:
        probe=SingleInstance()
        probe.close()
    except DesktopUnavailable:
        pytest.skip('实际助手正在运行，生命周期验收需先停止它，测试不会关闭用户的进程')
    try:
        result=run_cli(tmp_path,'start')
        assert result['running'] is True
        assert result['mode'] == 'observe'
        result=run_cli(tmp_path,'pause')
        assert result['mode'] == 'paused'
        result=run_cli(tmp_path,'stop')
        assert result['mode'] == 'stopped'
        assert not (tmp_path/'runtime'/'control.json').exists()
    finally:
        if (tmp_path/'runtime'/'control.json').exists():
            subprocess.run([sys.executable,'-m','xiaomao','--root',str(tmp_path),'stop'],capture_output=True,timeout=50)
