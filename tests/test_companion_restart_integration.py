from dataclasses import replace

import pytest
from test_engine import system
from test_resident import resident_for
from xiaomao.companion import CompanionPreferences
from xiaomao.companion_store import CompanionStore
from xiaomao.domain import Message


@pytest.mark.parametrize('stage',['followup','checkin'])
def test_safe_restart_and_enable_preserve_future_companion_schedule(system,stage):
    _,_,model,clock,_=system
    model.proactive=lambda *args:None
    live=resident_for(system);engine=live.engines[0]
    runtime=engine.companion
    runtime.configure(CompanionPreferences(enabled=True,quiet_start=0,quiet_end=0))
    runtime.observe(Message('最近在学摄影',message_id='in',created_at=clock()))
    runtime.replied('out',clock())
    if stage=='checkin':
        runtime.planner.state.update(followup_at=0,unanswered=True,unanswered_at=clock(),
                                     opener_at=clock(),checkin_at=clock()+600)
        runtime.save()
    field=stage+'_at';due=runtime.planner.state[field]
    live.stop_if_idle()
    assert CompanionStore(engine.settings.runtime,engine.settings.memory_scope).data['planner'][field]==due
    resumed=resident_for(system)
    try:
        resumed.enable()
        assert resumed.engines[0].companion.planner.state[field]==due
        resumed.pause()
        assert resumed.engines[0].companion.planner.state[field]==0
    finally:resumed.stop()
