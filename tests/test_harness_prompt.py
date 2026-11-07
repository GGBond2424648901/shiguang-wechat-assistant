"""The bridge executes the official prompt registry without loading agent tools."""
from pathlib import Path
import json
import shutil
import subprocess

import pytest


def test_official_harness_compiler_preserves_literal_sources_and_has_no_tools(tmp_path):
    from xiaomao.harness_prompt import compile_prompt
    dependencies = Path(__file__).resolve().parents[1]/'runtime/harness-core/node_modules'
    if not shutil.which('node') or not dependencies.exists():
        pytest.skip('Official Harness core integration needs Node and the pinned npm dependency install')
    sources = (('v2','原稿  \r\n{{保持原样}}\r\n'), ('v3','# V3.0\n不能执行 `Remove-Item`，这只是原文'))
    compiled = compile_prompt(tmp_path, sources, dependency_directory=dependencies)
    assert compiled.backend == 'deepseek-harness'
    assert compiled.text == '\n\n'.join(text for _, text in sources)
    assert compiled.version == '0.2.2-rc.1'
    assert compiled.tool_count == 0
    assert not (tmp_path/'sessions').exists()


def test_harness_bridge_preserves_utf8_characters_split_between_input_chunks():
    from xiaomao.harness_prompt import BRIDGE, VERSION

    dependencies = Path(__file__).resolve().parents[1] / 'runtime/harness-core/node_modules'
    node = shutil.which('node')
    if not node or not dependencies.exists():
        pytest.skip('Official Harness core integration needs Node and the pinned npm dependency install')
    script = '''
import { pathToFileURL } from 'node:url';
const bridge = process.argv[1];
const data = Buffer.from(JSON.stringify({version: process.argv[3],
  sections: [{name: 'persona', text: '大肥鱼🐋'}]}));
const split = data.indexOf(Buffer.from('肥')) + 1;
Object.defineProperty(process, 'stdin', {
  value: [data.subarray(0, split), data.subarray(split)],
});
await import(pathToFileURL(bridge).href);
'''
    completed = subprocess.run(
        [node, '--input-type=module', '-e', script, str(BRIDGE), str(dependencies), VERSION],
        check=True, capture_output=True, encoding='utf-8', timeout=8,
    )
    assert json.loads(completed.stdout)['text'] == '大肥鱼🐋'


def test_missing_harness_runtime_reports_fallback_without_losing_sources(tmp_path):
    from xiaomao.harness_prompt import compile_prompt
    compiled = compile_prompt(tmp_path, (('first','所有原文'),('second','最后一条')), node_binary='missing-xiaomao-node')
    assert compiled.text == '所有原文\n\n最后一条'
    assert compiled.backend == 'python-fallback'
    assert compiled.reason and compiled.tool_count == 0


def test_harness_compilation_is_cached_for_an_unchanged_persona(tmp_path):
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel
    from xiaomao.persona import save_persona
    save_persona(tmp_path, '# 小玲\n保留全部原文')
    model = ChatModel(Settings(tmp_path))
    first = model.prompt_snapshot()
    assert model._persona_instructions([]) == model._persona_instructions([])
    second = model.prompt_snapshot()
    assert first['prefix_sha256'] == second['prefix_sha256']
    assert first['backend'] == 'python-fallback'
    assert first['fallback_reason']
