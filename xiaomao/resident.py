"""独立常驻的多群协调器；每群状态隔离，共享一个串行桌面。"""
import threading
import time
import logging
import traceback
from pathlib import Path
from .engine import Engine
from .model import ChatModel
from .vision import Vision
from .windows import WindowsDesktop,NavigationActivity


class Resident:
    def __init__(self,settings,store,*,desktop_factory=None,vision=None,model_factory=ChatModel,clock=time.time,settle_seconds=5):
        self.settings,self.store,self.clock=settings,store,clock
        self.stop_event=threading.Event()
        self.lock=threading.RLock()
        self.index=self.turns=0
        self.navigation=NavigationActivity()
        self.database=None
        if desktop_factory is None and settings.receive_mode=='database' and settings.binding:
            from .database import DatabaseHub
            self.database=DatabaseHub(settings,self.stop_event)
        vision=vision or Vision()
        factory=desktop_factory or (lambda binding:WindowsDesktop(binding,navigation=self.navigation))
        self.engines=[]
        for name in settings.allowed_groups:
            cfg=settings.for_group(name)
            from .database import LazyDatabaseFeed
            source=LazyDatabaseFeed(self.database,name) if self.database else None
            self.engines.append(Engine(cfg,store,factory(cfg.binding),vision,model_factory(cfg),clock=clock,settle_seconds=settle_seconds,message_source=source))

    def status(self):
        states=[engine.status() for engine in self.engines]
        modes={s['mode'] for s in states}
        mode=next(iter(modes)) if len(modes)==1 else 'auto' if 'auto' in modes else 'observe' if 'observe' in modes else 'paused'
        current=states[self.index]
        last_result=max((s['last_result'] for s in states if s.get('last_result')),key=lambda r:r['at'],default=None)
        return {**states[0],'mode':mode,'reason':current['reason'],'active_group':current['group'],
                'last_result':last_result,
                'saved_groups':list(self.settings.saved_groups),'monitor_all_groups':self.settings.monitor_all_groups,
                'group_names':list(self.settings.allowed_groups),'pending_count':sum(s['pending_count'] for s in states),
                'last_observed':max((s['last_observed'] for s in states if s['last_observed'] is not None),default=None),
                'groups':[{'name':s['group'],'mode':s['mode'],'reason':s['reason'],'pending_count':s['pending_count']} for s in states]}

    def pending(self):
        return [{**task,'group':engine.settings.group} for engine in self.engines for task in engine.pending()][:20]

    def _control(self,operation,*args):
        for engine in self.engines:getattr(engine,operation)(*args)
        return self.status()

    def observe(self):return self._control('observe')
    def enable(self):return self._control('enable')
    def pause(self,reason='用户已暂停'):return self._control('pause',reason)

    def stop(self):
        with self.lock:
            result=self._control('stop')
            self.stop_event.set()
            return result

    def reply_latest(self):
        # 本机恢复入口只指向群列表第一项，绝不把所有群历史一起排队。
        with self.lock:self.index=self.turns=0
        self.engines[0].reply_latest()
        return self.status()

    def companion_preview(self):
        self.engines[0].companion_preview()
        return self.status()

    def companion_once(self):
        self.engines[0].companion_once()
        return self.status()

    def observe_once(self):
        if self.stop_event.is_set():return
        with self.lock:engine=self.engines[self.index]
        try:engine.observe_once()
        except Exception as error:
            # A live HTTP endpoint must not hide a dead observer thread.
            # Log only code locations/types, never chat/model/credential data.
            locations=' > '.join(f'{Path(f.filename).name}:{f.lineno}:{f.name}'
                                 for f in traceback.extract_tb(error.__traceback__)[-6:])
            logging.getLogger(__name__).error('Observer fault at %.3f: %s [%s]',
                                               self.clock(),type(error).__name__,locations)
            try:engine.pause('监听遇到内部错误，已暂停发送并记录原因；助手进程仍在运行')
            except Exception as cleanup_error:
                # A disk failure can also break pause's journal writes. The
                # cancellation fence must still stop input; keep tasks for
                # inspection rather than replaying an uncertain transaction.
                engine.cancelled.set()
                with engine.lock:
                    engine.mode='paused'
                    engine.reason='本机状态保存异常，已暂停发送；助手进程仍在运行'
                logging.getLogger(__name__).error('Observer pause cleanup failed: %s',type(cleanup_error).__name__)
        with self.lock:
            self.turns+=1
            # 一个时间片含选择与两帧核对；刚入队的任务再给一帧开始处理。
            limit=4 if engine.tasks else 3
            if engine.mode not in ('observe','auto') or self.turns>=limit:
                self.index=(self.index+1)%len(self.engines)
                self.turns=0

    def run(self):
        try:
            while not self.stop_event.is_set():
                self.observe_once()
                self.stop_event.wait(self.settings.poll_seconds)
        finally:
            for engine in self.engines:
                if engine.companion:engine.companion.close()
            if self.database:self.database.close()
