"""Nonblocking daily-news schedules and receipt-journaled WeChat delivery."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import threading
import time


BEIJING = timezone(timedelta(hours=8))
TERMINAL = {'verified', 'failed', 'unknown', 'empty', 'missed', 'cancelled'}
WINDOW = 2 * 3600


class NewsRuntime:
    def __init__(self, runtime, *, context, deliver, delivery_status, cancel_delivery,
                 research_factory, clock=time.time, store=None, executor=None, delivery_lookup=None):
        from .news_store import NewsStore
        self.store = store if store is not None else NewsStore(runtime)
        self.context = context
        self.deliver = deliver
        self.delivery_status = delivery_status
        self.delivery_lookup = delivery_lookup
        self.cancel_delivery = cancel_delivery
        self.research_factory = research_factory
        self.clock = clock
        self.executor = executor or ThreadPoolExecutor(max_workers=2, thread_name_prefix='news-research')
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.jobs = {}
        self._account = ''

    def _context(self):
        value = self.context()
        return {**value, 'account': value.get('account') or 'unbound'}

    def command(self, subscription, action, edition=None):
        if action not in ('preview', 'push', 'cancel'):
            raise ValueError('不支持这个资讯操作')
        with self.lock:
            if self.closed.is_set(): raise ValueError('资讯服务正在退出，请重新启动助手')
            context = self._context(); account = context['account']
            config = self.store.get_subscription(account, subscription)
            if config is None: raise ValueError('订阅不存在或微信账号已变化，请重新选择')
            if action == 'preview':
                for job_id, (owner, future, cancelled) in self.jobs.items():
                    row = self.store.get_edition(owner, job_id)
                    if owner == account and row and row['subscriptionId'] == subscription and not cancelled.is_set():
                        if row['snapshot'].get('revision') == config.get('revision'):
                            return {'accepted': True, 'edition': row['id']}
                        self._cancel(row, account, '订阅设置已更新')
                now = self.clock()
                row = self.store.create_edition(account, subscription, config, 'preview', '', now)
                row = self.store.update_edition(account, row['id'], deadline=now + WINDOW, publishRequested=False)
                self._submit(account, row)
                return {'accepted': True, 'edition': row['id']}
            row = self.store.get_edition(account, edition) if isinstance(edition, str) else None
            if not row or row['subscriptionId'] != subscription:
                raise ValueError('请先生成并选择本订阅的资讯预览')
            if action == 'cancel':
                self._cancel(row, account, '已停止本次推送')
                return {'accepted': True, 'edition': row['id']}
            if row['snapshot'].get('revision') != config.get('revision'):
                raise ValueError('订阅设置已更新，请重新生成预览')
            if row['state'] == 'verified' or row.get('publishRequested'):
                return {'accepted': True, 'edition': row['id']}
            if row['state'] != 'ready' or not row.get('articles'):
                raise ValueError('当前简报尚未就绪，请先生成预览')
            if self.clock() > row.get('deadline', row['createdAt'] + WINDOW):
                raise ValueError('这份预览已过期，请重新生成最新资讯')
            if not config.get('targets'):
                raise ValueError('请先选择推送目标并保存订阅')
            self.store.update_edition(account, row['id'], publishRequested=True, state='waiting', reason='等待推送')
            return {'accepted': True, 'edition': row['id']}

    def invalidate(self, account, subscription, reason='订阅设置已更新'):
        """Fence queued sends BEFORE editing/deleting a subscription in another process."""
        with self.lock:
            for row in self.store.pending(account):
                if row['subscriptionId'] == subscription:
                    self._cancel(row, account, reason)

    def _cancel(self, row, account, reason):
        job = self.jobs.get(row['id'])
        if job:
            job[2].set(); job[1].cancel()
        # Persist the cancellation fence before waiting for the native sender.
        # It checks this journal immediately before input and Send.
        self.store.update_edition(account, row['id'], state='cancelled', reason=reason, receiptPending=True)
        self._reconcile(row, account)
        ids = [part['taskId'] for delivery in row.get('deliveries', [])
               for part in delivery.get('parts', []) if part.get('taskId') and part.get('state') not in TERMINAL]
        if ids: self.cancel_delivery(ids)
        # Confirmed/unknown tasks are never reset or replayed. Cancellation only
        # prevents future parts; an already-started native transaction may finish.
        self._reconcile(row, account)
        receipt_pending = any(p['state'] == 'queued' for d in row.get('deliveries', []) for p in d['parts'])
        return self.store.update_edition(account, row['id'], state='cancelled', reason=reason, receiptPending=receipt_pending)

    def _submit(self, account, row):
        if row['id'] in self.jobs or len(self.jobs) >= 2 or self.closed.is_set(): return
        cancelled = threading.Event()
        in_flight = {}
        for pending in self.store.pending(account):
            for delivery in pending.get('deliveries', []):
                for part in delivery.get('parts', []):
                    if (part.get('state') in ('queued', 'dispatching') and part.get('url')
                            and (part.get('taskId') or part.get('requestId'))):
                        in_flight.setdefault(delivery['scope'], set()).add(part['url'])
        histories = [self.store.seen_urls(account, target['scope']) | self.store.uncertain_urls(account, target['scope'])
                     | in_flight.get(target['scope'], set()) for target in row['snapshot'].get('targets', [])]
        exclude_urls = set.intersection(*histories) if histories else set()
        def collect():
            research = self.research_factory()
            try:
                return research.collect(row['snapshot'], row['createdAt'],
                                        cancelled=lambda: cancelled.is_set() or self.closed.is_set(),
                                        exclude_urls=exclude_urls)
            finally:
                close = getattr(research, 'close', None)
                if close: close()
        future = self.executor.submit(collect)
        self.jobs[row['id']] = (account, future, cancelled)

    def _valid(self, account, row):
        config = self.store.get_subscription(account, row['subscriptionId'])
        return bool(config and config.get('revision') == row['snapshot'].get('revision')
                    and (row['trigger'] != 'scheduled' or config.get('enabled')))

    def tick(self):
        if self.closed.is_set(): return
        with self.lock:
            context = self._context(); account = context['account']; now = self.clock()
            if self._account and self._account != account:
                for row in self.store.pending(self._account):
                    self._cancel(row, self._account, '微信账号已变化，旧推送已取消')
            self._account = account
            for identity, (owner, future, cancelled) in list(self.jobs.items()):
                row = self.store.get_edition(owner, identity)
                if owner != account or not row or not self._valid(owner, row) or row['state'] == 'cancelled':
                    if row: self._cancel(row, owner, '订阅或微信账号已变化')
                    else: cancelled.set(); future.cancel()
                if not future.done(): continue
                self.jobs.pop(identity, None)
                if cancelled.is_set() or not row or row['state'] == 'cancelled': continue
                try:
                    result = future.result()
                    articles, parts = result['articles'], result['parts']
                    if not isinstance(articles, list) or not isinstance(parts, list) or len(articles) != len(parts):
                        raise ValueError('invalid_news_result')
                    warnings = result.get('warnings', [])[:24]
                    failed = not articles and any(any(code in warning for code in ('失败', '密钥', '凭据', '超时')) for warning in warnings)
                    self.store.update_edition(owner, identity, articles=articles, parts=parts, warnings=warnings,
                        state=('waiting' if row.get('publishRequested') else 'ready') if articles else 'failed' if failed else 'empty',
                        reason='' if articles else warnings[0] if failed else '没有找到符合方向与时效要求的公开资讯')
                except Exception as error:
                    logging.getLogger(__name__).info('News collection failed: %s', type(error).__name__)
                    self.store.update_edition(owner, identity, state='failed',
                                             reason='资讯采集失败，请检查模型连接后重新生成')
            self._schedule(account, now)
            pending_rows = self.store.pending(account)
            # Harvest EVERY native outcome first. A newer waiting edition must
            # see an older edition's just-confirmed URL before it can enqueue.
            for row in pending_rows:
                self._reconcile(row, account)
            for row in pending_rows:
                if row['state'] in TERMINAL or row['state'] == 'partial':
                    self._audit_terminal(row, account)
                    continue
                if not self._valid(account, row):
                    self._cancel(row, account, '订阅设置已更新或停用'); continue
                if row['state'] == 'collecting':
                    if now > row.get('deadline', row['createdAt'] + WINDOW):
                        self._cancel(row, account, '采集已超过本次推送时间窗口')
                    else: self._submit(account, row)
                elif row.get('publishRequested'):
                    self._pump(account, row, now)
                elif row['state'] == 'ready' and now > row.get('deadline', row['createdAt'] + WINDOW):
                    self.store.update_edition(account, row['id'], state='missed', reason='这份预览已过期，请重新生成最新资讯')

    def _schedule(self, account, now):
        from .news_schedule import due_schedule
        pending = self.store.pending(account)
        for config in self.store.list_subscriptions(account):
            if not config.get('enabled'): continue
            plan = due_schedule(config, now, window=WINDOW)
            if plan is None: continue
            # Keep the latest edition, rather than releasing every missed
            # interval on wake/resume. Native transactions still get audited.
            for older in pending:
                if (older['subscriptionId'] == config['id'] and older['trigger'] == 'scheduled'
                        and older['state'] in ('collecting', 'ready', 'waiting', 'publishing')
                        and self._scheduled_at(older) < plan['due']):
                    self._cancel(older, account, '下一轮推送计划已到，已停止旧资讯的后续内容')
            if self.store.get_slot(account, config['id'], plan['slot']): continue
            legacy = self.store.get_slot(account, config['id'], plan['legacyDay'])
            if (legacy and legacy.get('trigger') == 'scheduled'
                    and legacy['snapshot'].get('time', '09:00') == plan['time']):
                continue  # v0.3.16's daily record is already this occurrence.
            row = self.store.create_edition(account, config['id'], config, 'scheduled', plan['slot'], now)
            if row is None: continue
            if now >= plan['deadline']:
                self.store.update_edition(account, row['id'], state='missed', scheduledAt=plan['due'],
                                         reason='已错过本轮推送时间，等待下次计划')
                continue
            row = self.store.update_edition(account, row['id'], publishRequested=True,
                                           scheduledAt=plan['due'], deadline=plan['deadline'])
            self._submit(account, row)

    @staticmethod
    def _scheduled_at(row):
        if type(row.get('scheduledAt')) in (int, float): return row['scheduledAt']
        slot = row.get('slot', '')
        if 'T' not in slot: slot += 'T' + row['snapshot'].get('time', '09:00')
        try: return datetime.fromisoformat(slot).replace(tzinfo=BEIJING).timestamp()
        except (TypeError, ValueError): return row['createdAt']

    def _prepare_deliveries(self, account, row, now):
        from .news_research import format_parts
        deliveries = []
        for target in row['snapshot'].get('targets', []):
            seen = self.store.seen_urls(account, target['scope'])
            articles = [a for a in row['articles'] if a['url'] not in seen]
            parts = format_parts(row['snapshot'], articles, row['createdAt'])
            if len(parts) != len(articles): raise ValueError('资讯分段与来源不匹配')
            deliveries.append({**target, 'state': 'waiting' if parts else 'verified',
                'reason': '' if parts else '本轮来源均已向此目标确认，无新增内容',
                'parts': [dict(text=text, url=article['url'], requestId='', taskId='', state='waiting', reason='', attempt=0)
                          for text, article in zip(parts, articles)]})
        return self.store.update_edition(account, row['id'], deliveries=deliveries)

    def _reconcile(self, row, account):
        """Journal receipts before queuing a successor or fencing cancellation."""
        for delivery in row.get('deliveries', []):
            for part in delivery.get('parts', []):
                if not part.get('taskId') and part.get('requestId') and part['state'] not in TERMINAL:
                    # Queue intent was committed before native acceptance. A
                    # crash between the two journals recovers by stable ID,
                    # before any competing edition can queue the same URL.
                    if self.delivery_lookup:
                        found = self.delivery_lookup(delivery, part['requestId'])
                        if found.get('id'):
                            part.update(taskId=found['id'], state='queued')
                        else:
                            part['state'] = 'waiting'
                    else:
                        part.update(state='unknown', reason='无法核对中断的发送计划，未自动重发')
                        self.store.mark_uncertain(account, delivery['scope'], [part['url']], self.clock())
                if not part.get('taskId') or part['state'] in TERMINAL: continue
                status = self.delivery_status(part['taskId'])
                state = status.get('state')
                if state == 'verified':
                    part['state'] = 'verified'; part['reason'] = '微信回执已核对'
                    self.store.mark_seen(account, delivery['scope'], [part['url']], self.clock())
                elif state == 'unknown' or state == 'skipped' and status.get('attempted'):
                    part['state'] = 'unknown'; part['reason'] = '发送回执未能确认，请到微信核对，未自动重发'
                    self.store.mark_uncertain(account, delivery['scope'], [part['url']], self.clock())
                elif state == 'skipped':
                    if status.get('reason') in ('restart', 'paused') and part.get('attempt', 0) < 64:
                        part.update(state='waiting', taskId='', requestId='', attempt=part.get('attempt', 0) + 1,
                                    reason='正在恢复未发送的内容')
                    else:
                        part['state'] = 'failed'; part['reason'] = '本段发送已取消，请重新预览后推送'
                elif not status:
                    # A missing journal cannot prove that a send never occurred.
                    part['state'] = 'unknown'; part['reason'] = '发送记录缺失，请到微信核对，未自动重发'
                    self.store.mark_uncertain(account, delivery['scope'], [part['url']], self.clock())
                else: part['state'] = 'queued'
        if row.get('deliveries'):
            self.store.update_edition(account, row['id'], deliveries=row['deliveries'])

    def _audit_terminal(self, row, account):
        deliveries = row.get('deliveries', [])
        pending = any(p['state'] == 'queued' for d in deliveries for p in d['parts'])
        changes = dict(deliveries=deliveries, receiptPending=pending)
        if row['state'] != 'cancelled' and deliveries:
            for delivery in deliveries:
                parts = delivery['parts']
                if all(p['state'] == 'verified' for p in parts):
                    delivery.update(state='verified', reason='微信回执已核对')
                elif any(p['state'] == 'unknown' for p in parts):
                    delivery.update(state='unknown', reason='发送回执未能确认，请到微信核对')
            states = {d['state'] for d in deliveries}
            if states == {'verified'}:
                changes.update(state='verified', reason='所有目标的微信回执已核对')
            elif not pending and 'unknown' in states:
                changes.update(state='unknown', reason='存在待核对的微信回执，未自动重发')
        self.store.update_edition(account, row['id'], **changes)

    def _pump(self, account, row, now):
        if not row.get('deliveries'):
            row = self._prepare_deliveries(account, row, now)
        self._reconcile(row, account)
        expired = now >= row.get('deadline', row['createdAt'] + WINDOW)
        for delivery in row['deliveries']:
            parts = delivery['parts']
            if all(p['state'] == 'verified' for p in parts):
                delivery['state'] = 'verified'; continue
            if any(p['state'] in ('unknown', 'failed') for p in parts):
                delivery['state'] = 'unknown' if any(p['state'] == 'unknown' for p in parts) else 'failed'
                delivery['reason'] = next(p['reason'] for p in parts if p['state'] in ('unknown', 'failed'))
                continue
            context = self._context()
            target = next((t for t in context.get('targets', []) if t['session'] == delivery['session'] and t['scope'] == delivery['scope']), None)
            if context['account'] != account or target is None:
                self.cancel_delivery([p['taskId'] for p in parts if p.get('taskId') and p['state'] == 'queued'])
                delivery.update(state='failed', reason='目标身份或监听范围已变化，请重新选择')
                continue
            if expired:
                self.cancel_delivery([p['taskId'] for p in parts if p.get('taskId') and p['state'] == 'queued'])
                delivery.update(state='failed', reason='本次推送已过期，未继续发送旧资讯')
                continue
            if any(p['state'] == 'queued' for p in parts):
                delivery.update(state='publishing', reason='正在等待微信回执'); continue
            if target.get('mode') != 'auto':
                delivery.update(state='waiting', reason='目标已暂停或处于观察模式，等待恢复'); continue
            if not target.get('ready'):
                delivery.update(state='waiting', reason='等待校准目标窗口'); continue
            if target.get('busy'):
                delivery.update(state='waiting', reason='会话正在回复，资讯稍后发送'); continue
            part = next((p for p in parts if p['state'] == 'waiting'), None)
            if not part: continue
            # A different subscription can verify this URL while this one is
            # waiting behind ordinary chat. Recheck at the actual queue fence.
            if part['url'] in self.store.seen_urls(account, delivery['scope']):
                part.update(state='verified', deduplicated=True, reason='同一来源已在其他推送确认，本次跳过')
                continue
            quarantined = part['url'] in self.store.uncertain_urls(account, delivery['scope'])
            if quarantined:
                part.update(state='unknown', reason='该来源已有待核对的发送回执，未重复推送')
                delivery.update(state='unknown', reason=part['reason'])
                continue
            scope_hash = hashlib.sha256(delivery['scope'].encode()).hexdigest()[:12]
            request_id = f"news_{row['id']}_{scope_hash}_{parts.index(part)}_{part.get('attempt', 0)}"
            part.update(requestId=request_id, state='dispatching', reason='正在登记发送计划')
            self.store.update_edition(account, row['id'], deliveries=row['deliveries'])
            try:
                result = self.deliver(target, part['text'], request_id)
                if not isinstance(result.get('id'), str) or not result['id']:
                    raise ValueError('未获得有效的发送记录')
                part.update(requestId=request_id, taskId=result['id'], state='queued', reason='已加入发送队列')
                delivery.update(state='publishing', reason='正在等待微信回执')
            except ValueError:
                delivery.update(state='waiting', reason='会话暂不能发送，等待恢复或校准')
        states = {d['state'] for d in row['deliveries']}
        if states and states == {'verified'}:
            state, reason = 'verified', '所有目标的微信回执已核对'
        elif states and states <= {'verified', 'unknown', 'failed'}:
            state = 'unknown' if 'unknown' in states else 'partial' if 'verified' in states else 'failed'
            reason = '部分目标发送未完成，请查看推送记录'
        elif 'publishing' in states:
            state, reason = 'publishing', '正在逐条推送并核对微信回执'
        elif not states:
            state, reason = 'failed', '没有可推送的目标，请重新保存订阅'
        else:
            state, reason = 'waiting', '等待目标就绪；已确认的内容不会重复发送'
        pending = any(p['state'] == 'queued' for d in row['deliveries'] for p in d['parts'])
        self.store.update_edition(account, row['id'], deliveries=row['deliveries'], state=state,
                                 reason=reason, receiptPending=pending)

    def close(self):
        self.closed.set()
        for _, future, cancelled in list(self.jobs.values()):
            cancelled.set(); future.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)
