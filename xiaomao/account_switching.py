"""Account-scoped route migration; automatic verification never changes focus."""
from dataclasses import asdict, replace
from copy import deepcopy
import json
from .config import Settings, TARGET_GROUP
from .contacts import visual_name_key
from .member_names import safe_name


PROFILE_FIELDS = ('group','group_names','conversation_targets','monitor_all_groups',
                  'conversation_rules','paused_conversations','reply_mode','sticker_frequency',
                  'owner_private_token','self_display_name','self_name_unique_confirmed','at_note',
                  'calibration_account')


def configured_account(settings):
    accounts={t['account'] for t in settings.conversation_targets.values()}
    if settings.calibration_account:accounts.add(settings.calibration_account)
    if len(accounts)>1:raise ValueError('已保存会话的微信账号不一致')
    return next(iter(accounts),'')


def route_snapshot(settings):
    data=asdict(settings)
    # Profiles are JSON before DPAPI encryption. Canonicalize tuples here too,
    # otherwise a real encrypted roundtrip never matches the in-memory snapshot.
    return json.loads(json.dumps({key:data[key] for key in PROFILE_FIELDS},ensure_ascii=False))


def desired_account_profile(previous,account,display_name):
    """Retain desired subscriptions while the new account's directory loads.

    This is encrypted migration intent, never an active route or identity
    proof. prepare_account_settings must filter it against fresh contacts.
    """
    same=configured_account(previous)==account
    targets={name:{**value,'account':account} for name,value in previous.conversation_targets.items()}
    rules={}
    for name,target in previous.conversation_targets.items():
        rule=previous.conversation_rules.get(target['account']+'/'+target['id'])
        if rule:rules[account+'/'+target['id']]=deepcopy(rule)
    fresh=replace(previous,calibration=None,window_bindings={},calibration_account=account,
        conversation_targets=targets,conversation_rules=rules,paused_conversations=(),
        owner_private_token=previous.owner_private_token if same else '',
        self_display_name=previous.self_display_name if same else safe_name(display_name),
        self_name_unique_confirmed=False)
    for name in previous.saved_groups:
        if previous.is_conversation_paused(name):fresh=fresh.with_conversation_pause(name,True)
    return route_snapshot(fresh)


def prepare_account_settings(previous, observation, profile=None):
    """Keep only authenticated matching recipient IDs, never title lookalikes."""
    account=observation.account
    if (not isinstance(account,str) or not account or '/' in account or '\\' in account
            or any(ord(c)<32 for c in account) or type(observation.pid) is not int or observation.pid<=0):
        raise ValueError('当前微信账号身份无效')
    rows=tuple(observation.rows)
    if any(r.account!=account for r in rows) or len({r.id for r in rows})!=len(rows):
        raise ValueError('当前微信目录身份不一致')
    source=previous
    if profile is not None:
        if not isinstance(profile,dict) or set(profile)!=set(PROFILE_FIELDS):
            raise ValueError('账号配置字段无效')
        source=replace(previous,**deepcopy(profile),calibration=None,window_bindings={})
        if configured_account(source)!=account:raise ValueError('账号配置身份不一致')
    # Both IDs and types must agree. Duplicate visual labels are never auto-selected.
    labels={}
    for r in rows:labels[visual_name_key(r.name)]=labels.get(visual_name_key(r.name),0)+1
    contacts={(r.id,r.kind):r for r in rows if r.selectable and labels[visual_name_key(r.name)]==1}
    matched=[]
    for old_name in source.saved_groups:
        target=source.conversation_targets.get(old_name,{})
        contact=contacts.get((target.get('id'),target.get('kind')))
        if contact:matched.append((old_name,contact))
    names=tuple(r.name for _,r in matched) or (TARGET_GROUP,)
    primary=next((r.name for n,r in matched if n==source.group),names[0])
    rules={}
    for old_name,row in matched:
        old=source.conversation_targets[old_name]
        rule=source.conversation_rules.get(old['account']+'/'+old['id'])
        if rule:rules[account+'/'+row.id]=deepcopy(rule)
    returning=profile is not None
    same=configured_account(previous)==account
    fresh=replace(previous,group=primary,group_names=names,
        conversation_targets={r.name:r.binding for _,r in matched},
        monitor_all_groups=source.monitor_all_groups,conversation_rules=rules,paused_conversations=(),
        reply_mode=source.reply_mode,sticker_frequency=source.sticker_frequency,
        calibration=None,calibration_account=account,window_bindings={},
        account_display_name=safe_name(observation.display_name) or '当前微信',account_process_id=observation.pid,
        owner_private_token=source.owner_private_token if returning or same else '',
        self_display_name=source.self_display_name if returning or same else safe_name(observation.display_name),
        self_name_unique_confirmed=False,at_note='已自动识别微信账号；群昵称需按当前群核对')
    # A global manual pause follows the operator; a returning profile otherwise
    # owns its own individual room pauses. No account's memory is copied here.
    all_paused=all(previous.is_conversation_paused(n) for n in previous.saved_groups)
    for old_name,row in matched:
        if all_paused or source.is_conversation_paused(old_name):
            fresh=fresh.with_conversation_pause(row.name,True)
    if not matched and all_paused:fresh=fresh.with_conversation_pause(TARGET_GROUP,True)
    return fresh


def auto_bind_account(settings, observation, cancelled):
    """Reuse only fresh, twice-verified pixels for selected stable recipients.

    Hidden/minimized windows are left alone. A main window can supply a shared
    template after its current conversation has been checked against this
    account's directory. No window is restored, selected, clicked or activated.
    """
    from .selection import preview_window,verify_reviewed_window,bind_reviewed_window
    from .windows import window_cloaked
    targets={tuple(t[k] for k in ('account','id','kind')) for t in settings.conversation_targets.values()}
    if configured_account(settings)!=observation.account:return settings
    windows=[w for w in observation.windows if w.get('pid')==observation.pid
             and not w.get('minimized') and not w.get('cloaked')]
    # Native enumeration is z-order, not routing preference. A later main
    # window must never overwrite a verified independent conversation window.
    windows.sort(key=lambda w:w.get('kind')=='detached')
    main_count=sum(w.get('kind')=='main' for w in windows)
    for window in windows:
        if cancelled.is_set():break
        if window.get('kind')=='main' and main_count!=1:continue
        try:
            if window_cloaked(window['hwnd']):continue
            _,draft=preview_window(settings,window,'')
            checked,contact=verify_reviewed_window(settings,draft,cancelled)
            if not contact or (contact.account,contact.id,contact.kind) not in targets:continue
            if (checked.pid,checked.hwnd)!=(observation.pid,window['hwnd']):continue
            existing=settings.binding_for(contact.name)
            if checked.window_kind=='main' and existing and existing.window_kind=='detached':
                from .vision import detached_title_matches
                live=any(w.get('pid')==existing.pid and w.get('hwnd')==existing.hwnd
                    and w.get('kind')=='detached' and detached_title_matches(w.get('title',''),existing)
                    for w in observation.windows)
                if live:
                    settings=replace(settings,calibration=asdict(checked),calibration_account=contact.account)
                    continue
            settings=bind_reviewed_window(settings,checked,contact)
        except (ValueError,RuntimeError,OSError):
            # Incomplete render, drafts, old scroll position or ambiguous title:
            # receive remains available, the next stable observation can retry.
            continue
    return settings
