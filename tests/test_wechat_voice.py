import pytest

from xiaomao.domain import Message


def sent(text):
    return Message(text,direction='out',speaker_id='self')


def test_two_generic_openings_and_dots_do_not_reach_wechat():
    from xiaomao.wechat_voice import render_bubble
    first=render_bubble('行，那我再接他一句。',[],[])
    second=render_bubble('行，那面旗举稳了。',[sent(first)],[])
    assert first=='行吧，那我再接他一句' and second=='那面旗举稳了'
    assert render_bubble('行吧，那我先试。',[sent('行吧，下次见')],[])=='那我先试'
    assert render_bubble('好。',[],[]) not in ('好','好。','')
    assert render_bubble('行。明天过去。',[sent('行吧，下次见')],[])=='明天过去'
    assert render_bubble('嗯\n明天过去。',[],[])=='嗯呢，明天过去'


@pytest.mark.parametrize('body,source',[
    ('C:\\演示\\文件。','这个路径是什么？'),('git log -1 --format=测试。','给出命令'),
    ('python 测试。py','运行哪个命令？'),
    ('copy 文件。txt abc.txt','怎么复制文件？'),('customcli 测试。py','运行哪个命令？'),
    ('{"值":"行。"}','给我配置'),('原话：行。','原样返回'),('错误：未找到。','报错原文'),
    ('```python\nprint("好。")\n```','给我代码')])
def test_verbatim_content_is_never_restyled(body,source):
    from xiaomao.wechat_voice import render_bubble
    assert render_bubble(body,[],[Message(source)])==body


def test_quote_is_untouched_but_chat_punctuation_outside_it_is_removed():
    from xiaomao.wechat_voice import render_bubble,preserve_layout
    assert render_bubble('他说“好。”。',[],[])=='他说“好。”'
    assert render_bubble('第一句。第二句。  ',[],[])=='第一句\n第二句'
    assert render_bubble('  借到镜头啦\n',[],[])=='借到镜头啦'
    mixed='这是配置。\npattern = 好。\n保存后再试。'
    assert render_bubble(mixed,[],[])=='这是配置\npattern = 好。\n保存后再试'
    assert render_bubble('```txt\n原样。\n```\n外面是闲聊。',[],[])=='```txt\n原样。\n```\n外面是闲聊'
    assert render_bubble("他说'今天结束。明天继续。'。",[],[])=="他说'今天结束。明天继续。'"
    assert preserve_layout('文件路径是 ./assets/开心。png',[])
    assert render_bubble('文件路径是 ./assets/开心。png',[],[])=='文件路径是 ./assets/开心。png'
    for path in (r'.\assets\开心。png','assets/开心。png','images/头像/开心。png'):
        assert preserve_layout(path,[]) and render_bubble(path,[],[])==path


def test_faces_stay_optional_and_are_suppressed_near_other_faces_emoji_and_serious_tasks():
    from xiaomao.wechat_voice import render_bubble
    face='(｡･ω･｡)';body=f'这伞太好认了 {face}。'
    assert render_bubble(body,[],[]).endswith(face)
    assert face not in render_bubble(body,[sent('抓到啦 '+face)],[])
    assert face not in render_bubble('笑死 😸 '+face+'。',[],[])
    assert face not in render_bubble(body,[],[Message('数据库报错了，该怎么排查？')])
    assert render_bubble('再来一局啊。',[sent('试试啊'),sent('我在啊')],[])=='再来一局'
