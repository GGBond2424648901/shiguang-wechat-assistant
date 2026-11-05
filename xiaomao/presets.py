"""ChatLuna 的基础 YAML 预设格式适配；只替换文字变量，不执行模板代码。"""
from dataclasses import dataclass
from pathlib import Path
import re
import yaml
from .persona import validate_persona


def substitute(text,values):
    # 单次替换：用户正文内即使包含 {persona} 或表达式，也始终只是正文。
    return re.sub(r'\{(name|persona|sender|sender_id|prompt)\}',lambda m:values.get(m[1],m[0]),text)


@dataclass(frozen=True)
class Preset:
    prompts: tuple
    format_user_prompt: str

    def messages(self,name,persona):
        messages=[{'role':role,'content':substitute(content,{'name':name,'persona':persona})} for role,content in self.prompts]
        if not any(role=='system' and '{persona}' in content for role,content in self.prompts):
            messages.append({'role':'system','content':persona})
        return messages

    def user_message(self,sender,text):
        return substitute(self.format_user_prompt,{'sender':sender[:80] or '群友','sender_id':'','prompt':text[:2000]})


def load_preset(root: Path) -> Preset:
    path=Path(root)/'config/presets/xiaoling.yml'
    if not path.exists():return Preset((('system','{persona}'),),'{prompt}')
    if path.stat().st_size>65536:raise ValueError('人格预设文件过大')
    raw=path.read_text(encoding='utf-8');validate_persona(raw)
    try:data=yaml.safe_load(raw)
    except yaml.YAMLError:raise ValueError('人格预设YAML格式无效') from None
    if not isinstance(data,dict):raise ValueError('人格预设需为映射')
    prompts=data.get('prompts');form=data.get('format_user_prompt','{prompt}')
    if not isinstance(prompts,list) or not 1<=len(prompts)<=32 or not isinstance(form,str) or len(form)>2000:
        raise ValueError('人格预设缺少有效prompts或用户格式')
    parsed=[]
    for entry in prompts:
        if not isinstance(entry,dict) or entry.get('role') not in ('system','user','assistant') or not isinstance(entry.get('content'),str) or not entry['content'].strip():
            raise ValueError('人格预设只接受system/user/assistant文字')
        parsed.append((entry['role'],entry['content']))
    if not any(role=='system' for role,_ in parsed):raise ValueError('人格预设缺少系统设定')
    if '{prompt}' not in form:raise ValueError('用户格式必须包含{prompt}')
    return Preset(tuple(parsed),form)
