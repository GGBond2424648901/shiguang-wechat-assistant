"""Evidence-backed recall. A model cannot assign a speaker or invent source text."""
import hashlib
import json
import re
from .companion import local_day


def source_ref(value):return 'm_'+hashlib.sha256(value.encode()).hexdigest()[:16]


def _evidence_time(fact):
    resolution = fact.get('resolution')
    if fact['kind']=='thread' and fact.get('state')=='closed' and resolution and resolution.get('source'):
        return max(fact['at'], resolution.get('at', 0))
    return fact['at']


def bounded_facts(facts):
    """Keep the newest 40 pieces of evidence; favor preferences and open plans in the other 160 slots."""
    facts = list(facts)
    if len(facts)<=200:
        return facts
    chronological = sorted(facts, key=_evidence_time)
    def protected(fact):
        return fact['kind']=='preference' or (fact['kind']=='thread' and fact.get('state')=='open')
    # Stable sorts preserve the later observation when timestamps are tied.
    older = sorted(chronological[:-40], key=protected)[-160:]
    return sorted(older, key=_evidence_time) + chronological[-40:]


def accept_reflection(result,messages,existing,now):
    sources={source_ref(m.message_id):m for m in messages if m.message_id and m.direction=='in' and m.kind=='text'}
    facts=[dict(f) for f in existing]
    items=result.get('facts',[])
    items=items[:20] if isinstance(items,list) else []
    # One reflection batch may contain both the plan and its resolution;
    # JSON array order is not a chronological guarantee.
    items=sorted(items,key=lambda i:isinstance(i,dict) and i.get('state')=='closed')
    for item in items:
        if not isinstance(item,dict):continue
        if not isinstance(item.get('source'),str):continue
        m=sources.get(item.get('source'));quote=item.get('quote');kind=item.get('kind')
        if not m or not isinstance(quote,str) or not 3<=len(quote)<=240 or quote not in m.text or kind not in ('fact','preference','topic','feeling','interaction','thread'):continue
        # Unknown senders are retained only as a source-specific shared topic.
        if not re.fullmatch(r'p_[a-f0-9]{16}',m.speaker_id) and kind!='topic':continue
        if kind=='thread':
            if item.get('state') not in ('open','closed'):continue
            if item['state']=='closed':
                if item['source']==item.get('resolves'):continue
                target=next((f for f in facts if f['source']==item.get('resolves') and f['kind']=='thread'
                    and f['speaker']==m.speaker_id and f['at']<=m.created_at<=now),None)
                if target:
                    if target.get('resolution',{}).get('at',0)>m.created_at:continue
                    target.update(state='closed',resolution={'source':source_ref(m.message_id),'quote':quote,
                        'source_text':m.text[:2000],'at':m.created_at})
                continue
        # Keep negation, dates and qualifiers around a model-selected excerpt.
        fact={'source':source_ref(m.message_id),'speaker':m.speaker_id,'quote':quote,'kind':kind,'at':m.created_at,
              'source_text':m.text[:2000]}
        if kind=='thread':fact['state']='open'
        index=next((i for i,f in enumerate(facts) if f['source']==fact['source']
            and (f['quote']==quote or f['kind']==kind=='thread')),None)
        if index is None:facts.append(fact)
        elif facts[index].get('state')!='closed':facts[index]=fact
    diary=None
    refs=result.get('sources',[]);text=result.get('diary','')
    if isinstance(refs,list) and 0<len(refs)<=20 and all(isinstance(r,str) and r in sources for r in refs) and isinstance(text,str) and 0<len(text.strip())<=600:
        diary={'day':local_day(now),'text':text.strip(),'sources':refs,'at':now}
    return bounded_facts(facts),diary


def _tokens(text):
    words=re.findall(r'[a-z0-9_]+|[\u3400-\u9fff]+',text.lower())
    return {token for w in words for token in ([w] if len(w)<3 or w.isascii() else [w[i:i+2] for i in range(len(w)-1)])}


def recall(facts,query,speaker,*,now=None):
    tokens=_tokens(query)
    ranked=[];continuity=[];preferences=[]
    known=bool(re.fullmatch(r'p_[a-f0-9]{16}',speaker))
    for fact in facts:
        if fact['kind']!='topic' and (not known or fact['speaker']!=speaker):continue
        if now is not None and _evidence_time(fact)>now:continue
        if fact['kind']=='preference' and fact['at']>0:
            preferences.append(fact)
        if fact['kind'] in ('feeling','interaction','thread'):
            # A timestamped utterance is evidence of what someone expressed,
            # never a diagnosis or a claim that they still feel it now.
            recorded=_evidence_time(fact)
            age=now-recorded if now is not None else -1
            limit={'feeling':6*3600,'interaction':30*86400,'thread':14*86400}[fact['kind']]
            if recorded<=0 or age<0:continue
            if age<=limit:continuity.append(fact)
            elif fact['kind']!='thread' or fact.get('state')!='open':continue
            # Older unresolved topics remain available on a relevant cue.
            # Do not continually prompt the member about old unfinished plans.
        evidence=fact['quote']
        if fact['kind'] in ('preference','fact','thread'):
            # A selected excerpt can omit the subject of a correction.
            # Use this member's actual source and resolution, never infer a
            # conflict or erase a prior fact based on similar wording.
            evidence=fact.get('source_text') or evidence
            if fact.get('resolution'):
                evidence+=' '+fact['resolution']['source_text']
        score=len(tokens&_tokens(evidence))
        if not score:continue
        view=fact
        if fact['kind']=='topic' and now is not None and fact['at']>0:
            age=(now-fact['at'])/86400
            if age>=7:
                # Reversible detail -> gist -> dormant retrieval. The excerpt
                # alone can omit a negation or correction: never promote it to
                # a standalone fact. Preserve the original in encrypted notes.
                view={key:fact[key] for key in ('source','speaker','kind','at')}
                view['recall_phase']='dormant' if age>=60 else 'gist'
                if fact.get('source_text')==fact['quote']:view['quote']=fact['quote']
                score *= .25 if age>=60 else .5
        ranked.append((score,view))
    ranked.sort(key=lambda pair:(pair[0],_evidence_time(pair[1])),reverse=True)
    # Keep the latest changes together (hurt -> explanation -> repair). A
    # lexical match on the old hurt must not hide the later resolution.
    threads=sorted((f for f in continuity if f['kind']=='thread'),key=_evidence_time)[-2:]
    continuity=sorted((f for f in continuity if f['kind']!='thread'),key=lambda f:f['at'])[-3:]+threads
    selected=[]
    personal=(f for _,f in ranked if f['kind'] in ('preference','fact','thread'))
    latest=max(personal,key=_evidence_time,default=None)
    if latest is not None:selected.append(latest)
    # Expression preferences remain useful when the next topic has completely
    # different vocabulary. Keep the two newest original statements together
    # so a correction is visible, without merging people or rewriting history.
    for fact in sorted(preferences,key=_evidence_time,reverse=True)[:2]+sorted(continuity,key=_evidence_time,reverse=True):
        if len(selected)>=6:break
        if fact not in selected:selected.append(fact)
    for _,fact in ranked:
        if len(selected)>=6:break
        if fact not in selected:selected.append(fact)
    return '\n'.join(json.dumps(f,ensure_ascii=False) for f in sorted(selected,key=_evidence_time))
