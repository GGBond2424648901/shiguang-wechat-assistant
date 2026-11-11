"""Apply existing settings with the resident and queue guards shared by both UIs."""
from dataclasses import replace
from .client import ControlUnavailable
from .config import Settings
from .credentials import CredentialStore
from .reply_preferences import CONVERSATION_RULE_FIELDS, DEFAULT_RULE_FIELDS


def apply_saved_settings(client,original,updated,key=''):
    from .selection import stop_confirmed
    try:status=client.status()
    except ControlUnavailable:status={}
    mode=status.get('mode')
    if Settings.load(original.root)!=original:raise ValueError('设置已在其他窗口修改，请重新打开设置')
    rule_only=replace(original,**{k:getattr(updated,k) for k in DEFAULT_RULE_FIELDS})==updated
    if not key.strip() and rule_only and status.get('can_configure_reply_rules'):
        return client.configure_reply_defaults({k:getattr(updated,k) for k in DEFAULT_RULE_FIELDS
                                               if getattr(updated,k)!=getattr(original,k)})
    if (not key.strip() and status.get('can_configure_reply_rules') and
            replace(original,conversation_rules=updated.conversation_rules)==updated):
        changed=[scope for scope in original.conversation_rules.keys()|updated.conversation_rules.keys()
                 if original.conversation_rules.get(scope)!=updated.conversation_rules.get(scope)]
        if len(changed)==1:
            scope=changed[0]
            name=next((name for name,target in original.conversation_targets.items()
                       if target['account']+'/'+target['id']==scope),None)
            if name:
                before=original.conversation_rules.get(scope,{})
                after=updated.conversation_rules.get(scope,{})
                return client.configure_reply_rules(name,scope,{field:after.get(field) for field in CONVERSATION_RULE_FIELDS
                                                               if before.get(field)!=after.get(field)})
    route_fields=('group','group_names','monitor_all_groups','conversation_targets','calibration',
                  'calibration_account','window_bindings','paused_conversations')
    if 'idle_wake' in status:route_fields+=('idle_wake',)
    kept_scopes = {updated.conversation_scope(name) for name in updated.saved_groups}
    if updated.conversation_rules == {key: value for key, value in original.conversation_rules.items()
                                      if key in kept_scopes}:
        route_fields += ('conversation_rules',)
    if original.group!=updated.group and not updated.self_name_unique_confirmed:route_fields+=('self_name_unique_confirmed',)
    route_only=replace(original,**{k:getattr(updated,k) for k in route_fields})==updated
    if not key.strip() and route_only and 'window_reload_pending' in status:
        updated.save(expected=original)
        return client.reload_windows()
    if status.get('pending_count'):
        raise ValueError('仍有待发回复，已保留队列；监听范围可在线修改，其他设置请等本轮结束后保存')
    if status.get('can_stop_if_idle'):
        client.stop_if_idle()
        client.wait_stopped()
    else:stop_confirmed(original.root,client)
    if Settings.load(original.root)!=original:
        raise ValueError('设置已在等待期间更新，未覆盖新配置，请重新保存')
    if key.strip():CredentialStore(updated.runtime).set_key(key)
    updated.save(expected=original)
    return client.start(initial_mode=mode) if mode in ('paused','observe','auto') else client.start()
