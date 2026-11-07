from dataclasses import replace
import json
import pytest

from xiaomao import persona_modules as pm
from xiaomao.persona import load_persona, save_persona
from xiaomao.config import Settings
from xiaomao.model import ChatModel


def kit(root):
    return pm.save_modules(root, (
        pm.PersonaModule('v3','V3 语言与人格','# 原稿\r\n不能删掉任何一句\r\n',origin='用户原文'),
        pm.PersonaModule('v5','V5 主观能动性','# 想法\n有内容才延伸\n',origin='用户原文'),
    ), title='大肥鱼 · 模块化人格', expected_revision=None)


def test_modules_preserve_exact_text_and_compile_all_enabled_without_legacy_duplicate(tmp_path):
    save_persona(tmp_path,'旧版快照不能叠加两次')
    state=kit(tmp_path)
    text=load_persona(tmp_path)
    assert all(text.count(m.text)==1 for m in state.modules)
    assert '旧版快照不能叠加两次' not in text
    assert (tmp_path/'config/persona.md').read_text(encoding='utf-8')=='旧版快照不能叠加两次'
    assert pm.load_modules(tmp_path)==state
    exported=pm.export_modules(state)
    assert all(m.text in exported for m in state.modules)


def test_save_is_atomic_preserves_originals_and_rejects_stale_or_destructive_legacy_editor(tmp_path):
    before=kit(tmp_path)
    updated=pm.save_modules(tmp_path,[replace(before.modules[0],text='新的语言规则'),before.modules[1]],
                            title=before.title,expected_revision=before.revision)
    assert '新的语言规则' in load_persona(tmp_path)
    assert any(p.read_bytes()==before.modules[0].text.encode() for p in (tmp_path/'config/persona_sources').glob('*.md'))
    with pytest.raises(ValueError,match='其他|更新|版本'):
        pm.save_modules(tmp_path,before.modules,title=before.title,expected_revision=before.revision)
    with pytest.raises(ValueError,match='模块'):
        save_persona(tmp_path,'来自旧面板的覆盖')
    assert pm.load_modules(tmp_path)==updated


def test_disabling_is_explicit_and_does_not_delete_source_or_change_other_modules(tmp_path):
    before=kit(tmp_path)
    after=pm.save_modules(tmp_path,[before.modules[0],replace(before.modules[1],enabled=False)],
                          title=before.title,expected_revision=before.revision)
    assert before.modules[1].text not in load_persona(tmp_path)
    assert after.modules[1].text==before.modules[1].text
    assert after.modules[1].text in pm.export_modules(after,include_disabled=True)


def test_corrupt_manifest_never_falls_back_to_old_persona(tmp_path):
    kit(tmp_path)
    pm.manifest_path(tmp_path).write_text('{broken',encoding='utf-8')
    with pytest.raises(ValueError,match='模块'):
        load_persona(tmp_path)


def test_module_changes_invalidate_model_prefix_and_ignore_legacy_supplement(tmp_path):
    from xiaomao.persona_documents import import_persona_text
    before=kit(tmp_path)
    import_persona_text(tmp_path,'这是旧补充稿，不再额外叠加',title='旧稿',enabled=True)
    model=ChatModel(Settings(tmp_path))
    prefix=model._persona_instructions([])
    assert '这是旧补充稿' not in prefix
    after=pm.save_modules(tmp_path,[before.modules[0],replace(before.modules[1],text='全新观点规则')],
                          title=before.title,expected_revision=before.revision)
    assert '全新观点规则' in model._persona_instructions([])
    assert '有内容才延伸' not in model._persona_instructions([])
    assert model.prompt_snapshot()['prefix_sha256']
    (tmp_path/'config/persona_documents.json').write_text('{invalid old unused manifest',encoding='utf-8')
    assert '全新观点规则' in model._persona_instructions([])


@pytest.mark.parametrize('change',[
    lambda m:[m[0],m[0]],
    lambda m:[replace(m[0],text='sk-should-never-be-stored')],
    lambda m:[replace(m[0],text='')],
    lambda m:[replace(x,enabled=False) for x in m],
])
def test_invalid_module_set_does_not_modify_active_source(tmp_path,change):
    old=kit(tmp_path)
    with pytest.raises(ValueError):
        pm.save_modules(tmp_path,change(old.modules),title=old.title,expected_revision=old.revision)
    assert pm.load_modules(tmp_path)==old
