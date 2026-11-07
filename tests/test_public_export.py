from pathlib import Path
import importlib.util
import sys
import pytest


def exporter(monkeypatch):
    directory=Path(__file__).resolve().parents[1]/'packaging'
    monkeypatch.syspath_prepend(str(directory))
    spec=importlib.util.spec_from_file_location('public_export',directory/'export_public.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('name',['runtime/control.json','config/settings.json','design/draft.png','assets/private.png','user.dpapi','../outside.py'])
def test_export_rejects_runtime_private_and_design_files(monkeypatch,name):
    with pytest.raises(ValueError):exporter(monkeypatch).review_names([Path(name)])


def test_export_preserves_approved_brand_and_is_rebuildable(tmp_path,monkeypatch):
    module=exporter(monkeypatch)
    target=tmp_path/'public'
    module.export(target,tmp_path/'source.zip')
    assert not any((target/n).exists() for n in ('runtime','design','assets'))
    for name in ('app.png','app.ico','persona-avatar.png'):
        assert (target/'xiaomao/ui_assets'/name).read_bytes()==(module.ROOT/'xiaomao/ui_assets'/name).read_bytes()
    assert not (target/'config/settings.json').exists()
    assert (target/'config/persona.md').read_bytes()==(target/'xiaomao/_vendor/dafeiyu/persona-wechat.md').read_bytes()
    assert (target/'xiaomao/ui_assets/表情包收集说明.txt').is_file()
    # Public regression tests must carry their local fixture/helper imports.
    import ast
    for test in (target/'tests').glob('test_*.py'):
        tree=ast.parse(test.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom) and node.module and node.module.startswith('test_'):
                assert (target/'tests'/(node.module+'.py')).is_file(), (test.name,node.module)
            if isinstance(node,ast.ImportFrom) and node.module and node.module.startswith('scripts.'):
                assert (target/(node.module.replace('.','/')+'.py')).is_file(), (test.name,node.module)
    monkeypatch.setattr(module,'ROOT',target);monkeypatch.setattr(module,'MANIFEST',target/'packaging/public-files.txt')
    module.export(tmp_path/'again')
    assert (tmp_path/'again/xiaomao/ui_assets/app.png').read_bytes()==(target/'xiaomao/ui_assets/app.png').read_bytes()
