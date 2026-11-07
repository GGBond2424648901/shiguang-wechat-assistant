"""Build a real model with test-only memories and the configured HTTPS credentials."""
from dataclasses import replace
from pathlib import Path
import shutil
import time
import uuid

from xiaomao.model import ChatModel
from xiaomao.credentials import CredentialStore


def require_empty_selfchat(desktop,vision):
    for _ in range(5):
        visible=desktop.capture()
        frame=vision.read_controls(visible,desktop.calibration)
        if not frame.safe or frame.group!='文件传输助手':
            raise RuntimeError('实测目标窗口不是文件传输助手')
        if frame.draft_empty:return
        if frame.draft:raise RuntimeError('文件传输助手中有文字草稿，保留原文')
        painted=desktop.confirm_empty_editor(visible,vision)
        if painted is not None:return
        time.sleep(.3)
    raise RuntimeError('文件传输助手输入区无法独立确认为空，本次未执行发送')


def verify_filehelper(rows,account):
    """Require an unambiguous WeChat system service, including self-name collisions."""
    from xiaomao.contacts import SERVICE_DISABLED,visual_name_key
    title='文件传输助手'
    matches=[r for r in rows if visual_name_key(r.name)==visual_name_key(title)]
    if len(matches)!=1 or not (matches[0].id=='filehelper' and matches[0].account==account
            and matches[0].kind=='service' and matches[0].name==title and matches[0].reason==SERVICE_DISABLED):
        raise ValueError('无法唯一确认文件传输助手，不执行微信实测')


def selfchat_feed(hub):
    from xiaomao.database import DatabaseFeed
    hub._ensure(force_identity=True)
    account=hub.account['id']
    def identity():
        hub._ensure(force_identity=True)
        verify_filehelper(hub.directory(),account)
    identity()
    base=Path(hub.account['path'])/'db_storage/message'
    feed=DatabaseFeed(lambda:[hub._snapshot(p) for p in sorted(base.glob('message_[0-9]*.db'))],
                      'filehelper',hub.account['wxid'])
    feed.assert_identity=identity
    return feed


def isolated_model(original,cfg=None):
    qa=(original.runtime/'qa').resolve()
    if cfg is None:
        cfg=replace(original,root=qa/('probe-'+uuid.uuid4().hex),group='模型验收',group_names=(),
                    conversation_targets={},calibration=None,owner_private_token='',auto_on_launch=False)
    target_root=cfg.root.resolve()
    if not target_root.is_relative_to(qa) or target_root==qa or cfg.model_base_url!=original.model_base_url:
        raise ValueError('模型实测必须使用独立 QA 目录和相同服务端点')
    for name in ('config/persona.md','config/persona_modules.json','config/presets/xiaoling.yml'):
        source=original.root/name
        if source.is_file():
            target=cfg.root/name;target.parent.mkdir(exist_ok=True,parents=True);shutil.copyfile(source,target)
    return ChatModel(cfg,credential_store=CredentialStore(original.runtime))
