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
    idle_min_minutes: int = 50
    idle_max_minutes: int = 70
    followup_min_minutes: int = 2
    followup_max_minutes: int = 5
    checkin_min_minutes: int = 8
    checkin_max_minutes: int = 15
    signoff_min_minutes: int = 50
    signoff_max_minutes: int = 70
    idle_probability: float = .2
    followup_probability: float = .55

    def __post_init__(self):
        if any(type(v) is not bool for v in (self.enabled,self.followup,self.idle,self.gentle_followups)):
            raise ValueError('开关必须是布尔值')
        if type(self.daily_max) is not int or not 1<=self.daily_max<=24:
            raise ValueError('每天主动次数应为1至24次')
        for kind,label in (('idle','空闲话题'),('followup','聊后续话'),('checkin','无人回应追问'),('signoff','开场后收尾')):
            low,high=getattr(self,kind+'_min_minutes'),getattr(self,kind+'_max_minutes')
            if type(low) is not int or type(high) is not int or not 1<=low<=high<=1440:
                raise ValueError(label+'请填1至1440分钟，最短不能大于最长')
        if self.signoff_min_minutes<self.checkin_max_minutes:
            raise ValueError('开场后收尾的最短等待不能早于追问的最长等待')
        for chance in (self.idle_probability,self.followup_probability):
            if type(chance) not in (int,float) or not 0<=chance<=1:
                raise ValueError('开口概率应为0至100%')
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
        # Persisted future timers survive restart; the revision invalidates old
        # candidates. Expired follow-ups are consumed by decide, never replayed.
        self.state['revision']+=1
        self.reason='等待启动稳定'

    @property
    def due_at(self):
        p,s=self.preferences,self.state
        if not p.enabled:return 0
        if s['unanswered']:
            return (s['checkin_at'] or s['signoff_at']) if p.gentle_followups else 0
        if not s['last_in']:return 0
        due=[]
        if p.followup and p.followup_probability and s['followup_at']:due.append(s['followup_at'])
        if p.idle and p.idle_probability:
            due.append(max(s['next_idle'],s['last_activity']+p.idle_min_minutes*60))
        return min(due) if due else 0

    def _interval(self,kind):
        p=self.preferences
        return self.rng.uniform(getattr(p,kind+'_min_minutes')*60,getattr(p,kind+'_max_minutes')*60)

    def configure(self,preferences,now):
        old,self.preferences=self.preferences,preferences
        s=self.state
        s['revision']+=1
        if not preferences.enabled:
            s['followup_at']=0
            self.end_sequence()
            self.reason='主动聊天未开启'
            return
        # Change only the affected timers; editing interests must not silently
        # consume the one permitted check-in or sign-off.
        def changed(kind):
            return any(getattr(old,kind+suffix)!=getattr(preferences,kind+suffix)
                       for suffix in ('_min_minutes','_max_minutes'))
        if preferences.idle and (not old.enabled or not old.idle or changed('idle')):
            s['next_idle']=now+self._interval('idle')
        if not preferences.followup:s['followup_at']=0
        elif s['followup_at'] and changed('followup'):s['followup_at']=now+self._interval('followup')
        if not preferences.gentle_followups:self.end_sequence()
        elif s['unanswered']:
            if s['checkin_at'] and changed('checkin'):s['checkin_at']=now+self._interval('checkin')
            if s['signoff_at'] and changed('signoff'):
                s['signoff_at']=max(now+60,s['opener_at']+self._interval('signoff'))
        self.reason='策略已更新，等待下次合适时机'

    def end_sequence(self):
        self.state.update(checkin_at=0,signoff_at=0,opener_at=0)

    def observe(self,message,baseline=False):
        key=message.message_id
        if not key or key in self.state['seen']:return
        unanswered_at=self.state.get('unanswered_at') or self.state['last_activity']
        self.state['seen']=(self.state['seen']+[key])[-256:]
        if message.created_at>self.state['last_activity']:
            self.state['last_activity']=message.created_at
            self.state['next_idle']=message.created_at+self._interval('idle')
        if message.direction=='in':
            self.state['last_in']=max(self.state['last_in'],message.created_at)
            if baseline and self.state['unanswered'] and message.created_at>unanswered_at:
                self.state['unanswered']=False
                self.end_sequence()
                self.state['next_idle']=max(self.state['next_idle'],self.boot_at+self._interval('idle'))
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
        self.state['followup_at']=now+self._interval('followup') if self.preferences.followup else 0
        self.state['next_idle']=now+self._interval('idle')

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
            if p.followup and now-due<120 and self.rng.random()<p.followup_probability:kind='followup'
        if not kind and p.idle and now>=max(s['next_idle'],s['last_activity']+p.idle_min_minutes*60):
            s['next_idle']=now+self._interval('idle')
            if self.rng.random()<p.idle_probability:kind='idle'
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
                self.state['signoff_at']=max(now+60,self.state['opener_at']+self._interval('signoff'))
            return
        self.state['opener_at']=now
        self.state['checkin_at']=now+self._interval('checkin')
