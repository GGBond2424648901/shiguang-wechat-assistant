"""Pure, persistent companionship scheduler. It never sends or calls a model."""
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import random
import uuid

LOCAL = timezone(timedelta(hours=8))


def local_day(now):
    return datetime.fromtimestamp(now, LOCAL).strftime('%Y-%m-%d')


@dataclass(frozen=True)
class CompanionPreferences:
    enabled: bool = False
    daily_max: int = 2
    quiet_start: int = 23*60
    quiet_end: int = 9*60
    followup: bool = True
    idle: bool = True
    gentle_followups: bool = True
    style: str = ''
    interests: str = ''
    dates: list = field(default_factory=list)

    def __post_init__(self):
        if any(type(v) is not bool for v in (self.enabled,self.followup,self.idle,self.gentle_followups)):
            raise ValueError('开关必须是布尔值')
        if type(self.daily_max) is not int or not 1<=self.daily_max<=3:
            raise ValueError('每天主动次数应为1至3次')
        if any(type(v) is not int or not 0<=v<1440 for v in (self.quiet_start,self.quiet_end)):
            raise ValueError('免打扰时间无效')
        if any(not isinstance(v,str) or len(v)>limit for v,limit in ((self.style,2000),(self.interests,500))):
            raise ValueError('人格补充或兴趣过长')
        if not isinstance(self.dates,list) or len(self.dates)>8:raise ValueError('最多8个重要日期')
        try:
            for entry in self.dates:
                if set(entry)!={'date','label'} or not isinstance(entry['label'],str) or not 1<=len(entry['label'])<=80:raise ValueError()
                date=datetime.strptime('2000-'+entry['date'],'%Y-%m-%d')
                if date.strftime('%m-%d')!=entry['date']:raise ValueError()
        except (ValueError,TypeError,KeyError):raise ValueError('重要日期格式应为 MM-DD 和简短名称') from None


def quiet_now(now,prefs):
    value=datetime.fromtimestamp(now,LOCAL)
    minute=value.hour*60+value.minute
    a,b=prefs.quiet_start,prefs.quiet_end
    return (a<=minute<b if a<b else minute>=a or minute<b) if a!=b else False


def fresh_state():
    return dict(revision=0,seen=[],last_in=0,last_activity=0,followup_at=0,
                last_reply='',next_idle=0,unanswered=False,unanswered_at=0,high_water=0,dates_seen=[],
                checkin_at=0,signoff_at=0,opener_at=0)


@dataclass(frozen=True)
class Candidate:
    id: str
    kind: str
    created: float
    expires: float
    revision: int
    day: str
    label: str = ''


class Planner:
    def __init__(self,preferences,state,*,boot_at,rng=None):
        self.preferences=preferences
        self.state=state
        for key,value in fresh_state().items():state.setdefault(key,value)
        self.boot_at=boot_at
        self.rng=rng or random.SystemRandom()
        self._delivered_id=''
        # Restart cannot replay an old follow-up or in-flight generation.
        self.state['followup_at']=0
        self.state['revision']+=1
        self.reason='等待启动稳定'

    @property
    def due_at(self):
        s=self.state
        if s['unanswered']:return s['checkin_at'] or s['signoff_at']
        return s['followup_at'] or max(s['next_idle'],s['last_activity']+3000)

    def end_sequence(self):
        self.state.update(checkin_at=0,signoff_at=0,opener_at=0)

    def observe(self,message,baseline=False):
        key=message.message_id
        if not key or key in self.state['seen']:return
        unanswered_at=self.state.get('unanswered_at') or self.state['last_activity']
        self.state['seen']=(self.state['seen']+[key])[-256:]
        if message.created_at>self.state['last_activity']:
            self.state['last_activity']=message.created_at
            self.state['next_idle']=message.created_at+self.rng.uniform(3000,4200)
        if message.direction=='in':
            self.state['last_in']=max(self.state['last_in'],message.created_at)
            if baseline and self.state['unanswered'] and message.created_at>unanswered_at:
                self.state['unanswered']=False
                self.end_sequence()
                self.state['next_idle']=max(self.state['next_idle'],self.boot_at+self.rng.uniform(3000,4200))
                self.state['revision']+=1
            if not baseline:
                self.state['revision']+=1
                self.state['followup_at']=0
                self.state['unanswered']=False
                self.end_sequence()

    def replied(self,message_id,now):
        if message_id==self.state['last_reply']:return
        self.state['last_reply']=message_id
        self.state['last_activity']=max(now,self.state['last_activity'])
        self.state['followup_at']=now+self.rng.uniform(120,300)
        self.state['next_idle']=now+self.rng.uniform(3000,4200)

    def _clock_ok(self,now):
        if now<self.state['high_water']:
            self.state['revision']+=1
            self.state['followup_at']=0
            self.end_sequence()
            self.reason='系统时间回拨，等待时间恢复'
            return False
        self.state['high_water']=now
        return True

    def valid(self,candidate,now,*,continuing=False):
        waiting_for_reply=continuing or (candidate and candidate.kind in ('checkin','signoff'))
        return bool(candidate and self._clock_ok(now) and self.preferences.enabled
            and self.state['unanswered']==waiting_for_reply and (not continuing or self._delivered_id==candidate.id)
            and candidate.revision==self.state['revision']
            and candidate.created<=now<=candidate.expires and candidate.day==local_day(now)
            and not quiet_now(now,self.preferences))

    def decide(self,now):
        if not self._clock_ok(now):return
        p,s=self.preferences,self.state
        if not p.enabled:self.reason='主动聊天未开启';return
        if now-self.boot_at<60:self.reason='等待启动稳定';return
        if quiet_now(now,p):
            self.end_sequence()
            self.reason='免打扰时段';return
        if s['unanswered']:
            for field,kind in (('checkin_at','checkin'),('signoff_at','signoff')):
                due=s[field]
                if not due or not p.gentle_followups:continue
                if now<due:
                    self.reason='等一会儿再轻轻问一句' if kind=='checkin' else '等一会儿再收尾'
                    return
                s[field]=0
                if now-due>120 or local_day(now)!=local_day(s['opener_at']):
                    self.end_sequence();break
                return Candidate(uuid.uuid4().hex,kind,now,now+120,s['revision'],local_day(now))
            self.reason='这轮已经收住，等对方再开口';return
        if not s['last_in']:self.reason='等待本会话有聊天记录';return
        kind,label='', ''
        today=local_day(now)
        for entry in p.dates:
            key=today+':'+entry['date']+':'+entry['label']
            if entry['date']==today[5:] and key not in s['dates_seen']:
                s['dates_seen']=(s['dates_seen']+[key])[-32:]
                kind,label='date',entry['label'];break
        if not kind and s['followup_at'] and now>=s['followup_at']:
            due=s['followup_at'];s['followup_at']=0
            if p.followup and now-due<120 and self.rng.random()<.55:kind='followup'
        if not kind and p.idle and now>=max(s['next_idle'],s['last_activity']+3000):
            s['next_idle']=now+1800
            if self.rng.random()<.2:kind='idle'
        if not kind:self.reason='安静陪伴，等待合适话题';return
        self.reason='正在判断是否适合开口'
        return Candidate(uuid.uuid4().hex,kind,now,now+120,s['revision'],today,label)

    def delivered(self,candidate,now):
        self._delivered_id=candidate.id
        self.state['unanswered']=True
        self.state['unanswered_at']=now
        self.state['followup_at']=0
        self.state['last_activity']=max(now,self.state['last_activity'])
        self.reason='主动消息已发送，等待自然回应'

    def confirmed(self,candidate,now):
        self.delivered(candidate,now)
        if not self.preferences.gentle_followups:return
        if candidate.kind=='signoff':self.end_sequence();return
        if candidate.kind=='checkin':
            if self.state['opener_at']:
                self.state['signoff_at']=self.state['opener_at']+self.rng.uniform(3000,4200)
            return
        self.state['opener_at']=now
        self.state['checkin_at']=now+self.rng.uniform(8*60,15*60)
