"""Narrow official DeepSeek Harness prompt-assembly adapter, with no agent loop.

Only the pinned SystemPrompt registry is loaded. Credentials, conversation
messages, tools, filesystems, shells, sessions and model adapters are not mounted.
Compile on persona revision changes and reuse the result for subsequent replies.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from .persona_documents import file_revision


VERSION = '0.2.2-rc.1'
BRIDGE = Path(__file__).with_name('_vendor')/'deepseek_harness'/'compile.mjs'


@dataclass(frozen=True)
class PromptCompilation:
    text: str
    backend: str
    version: str = ''
    tool_count: int = 0
    reason: str = ''

    def snapshot(self):
        return dict(backend=self.backend, version=self.version, tool_count=self.tool_count,
                    fallback_reason=self.reason, prefix_sha256=hashlib.sha256(self.text.encode()).hexdigest(),
                    prefix_characters=len(self.text), prefix_bytes=len(self.text.encode()))


def _dependency_directory(root):
    local = Path(root)/'runtime'/'harness-core'/'node_modules'
    packaged = BRIDGE.parent/'node_modules'
    return local if local.exists() else packaged


def compiler_revision(root):
    deps = _dependency_directory(root)
    return (shutil.which('node'), str(deps), file_revision(deps/'@deepseek-ai/dsh-system-prompt/package.json'),
            file_revision(BRIDGE))


def compile_prompt(root, sections, *, dependency_directory=None, node_binary=None):
    sections = tuple(sections)
    fallback = '\n\n'.join(text for _,text in sections if text)
    node = shutil.which(node_binary or 'node')
    if not node:
        return PromptCompilation(fallback, 'python-fallback', reason='node_unavailable')
    deps = Path(dependency_directory) if dependency_directory is not None else _dependency_directory(root)
    if not (deps/'@deepseek-ai/dsh-system-prompt/package.json').is_file():
        return PromptCompilation(fallback, 'python-fallback', reason='dependency_unavailable')
    payload = json.dumps(dict(version=VERSION, sections=[dict(name=name,text=text) for name,text in sections]), ensure_ascii=False)
    # Environment variables may contain saved shell credentials. The compiler
    # needs only OS paths; never inherit key/provider/plugin configuration.
    environment = {key:value for key,value in os.environ.items() if key.upper() in {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP'}}
    try:
        completed = subprocess.run([node, str(BRIDGE), str(deps.resolve())], input=payload, text=True,
            encoding='utf-8', capture_output=True, timeout=8, env=environment, shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        result = json.loads(completed.stdout) if completed.returncode==0 else None
        if (not isinstance(result,dict) or result.get('text') != fallback or result.get('version') != VERSION
                or result.get('toolCount') != 0 or result.get('contextCount') != 0):
            return PromptCompilation(fallback, 'python-fallback', reason='invalid_compiler_output')
        return PromptCompilation(result['text'], 'deepseek-harness', VERSION)
    except (OSError, subprocess.SubprocessError, ValueError):
        return PromptCompilation(fallback, 'python-fallback', reason='compiler_unavailable')
