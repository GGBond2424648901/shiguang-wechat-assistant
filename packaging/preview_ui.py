"""Capture the real UI using fictional data on an isolated Windows desktop.

No WeChat process, existing user profile, model API or saved credential is used.
"""
import argparse
import ctypes
from ctypes import wintypes as W
from dataclasses import asdict
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'docs/screenshots')
    output=parser.parse_args().output;output.mkdir(parents=True,exist_ok=True)
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.CreateDesktopW.argtypes=[W.LPCWSTR,W.LPCWSTR,ctypes.c_void_p,W.DWORD,W.DWORD,ctypes.c_void_p]
    user.CreateDesktopW.restype=W.HANDLE;user.SetThreadDesktop.argtypes=[W.HANDLE]
    desktop=user.CreateDesktopW('XiaomaoPreview'+str(os.getpid()),None,None,0,0x1ff,None)
    if not desktop or not user.SetThreadDesktop(desktop):raise RuntimeError('Cannot create isolated preview desktop')
    import tkinter as tk
    import win32gui,win32ui
    from PIL import Image,ImageDraw
    from brand_assets import create_assets
    from xiaomao.config import Settings,Calibration
    from xiaomao.gui import Panel,default_regions
    from xiaomao.sticker_import import StickerShelf,inspect_picture,annotation

    groups=('读书分享','摄影交流','小夏')
    class DemoClient:
        def __init__(self,root):self.mode='auto'
        def status(self):
            return dict(running=True,mode=self.mode,group=groups[0],active_group=groups[0],group_names=list(groups),
                        reason='数据库后台监听中',pending_count=0,counts={'verified':12},receive_mode='database',
                        calibrated=True,window_reload_pending=False,can_stop_if_idle=True,reply_mode='natural',
                        groups=[dict(name=n,mode=self.mode,pending_count=0,reason='数据库后台监听中',
                                     calibrated=True,window_kind='detached',window_state='在后台') for n in groups])
        def start(self,initial_mode=None):
            if initial_mode:self.mode=initial_mode
            return self.status()
        def pending(self):return {'tasks':[]}
        def stop_if_idle(self):self.mode='stopped'
        def wait_stopped(self):pass
        def reload_windows(self):return self.status()
        def enable(self):self.mode='auto';return self.status()
        def pause(self):self.mode='paused';return self.status()

    def pump(window,seconds=.5):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:window.update();time.sleep(.02)

    def shot(window,name):
        pump(window)
        hwnd=win32gui.GetAncestor(window.winfo_id(),2)
        l,t,r,b=win32gui.GetWindowRect(hwnd);width,height=r-l,b-t
        dc=win32gui.GetWindowDC(hwnd);source=win32ui.CreateDCFromHandle(dc);target=source.CreateCompatibleDC()
        bitmap=win32ui.CreateBitmap();bitmap.CreateCompatibleBitmap(source,width,height);old=target.SelectObject(bitmap)
        try:
            if not user.PrintWindow(hwnd,target.GetSafeHdc(),2):raise RuntimeError('Cannot capture preview')
            Image.frombuffer('RGB',(width,height),bitmap.GetBitmapBits(True),'raw','BGRX',0,1).save(output/(name+'.png'))
        finally:
            target.SelectObject(old);win32gui.DeleteObject(bitmap.GetHandle());target.DeleteDC();source.DeleteDC();win32gui.ReleaseDC(hwnd,dc)

    with tempfile.TemporaryDirectory() as folder:
        folder=Path(folder);brand=folder/'brand';create_assets(brand)
        targets={n:dict(account='demo',id='demo'+str(i)+('@chatroom' if i<2 else ''),kind='group' if i<2 else 'private') for i,n in enumerate(groups)}
        cfg=Settings(folder,group=groups[0],group_names=groups,monitor_all_groups=True,conversation_targets=targets,reply_mode='natural')
        cfg.sticker_frequency='expressive'
        cfg=cfg.with_conversation_rules(groups[1],{'reply_mode':'mentions','sticker_frequency':'occasional'})
        cfg=cfg.with_conversation_rules(groups[2],{'reply_mode':'all','sticker_frequency':'requested'})
        cfg.window_bindings={'demo/'+targets[n]['id']:asdict(Calibration(i+1,1,900,700,n,default_regions(),True,n,targets[n]['kind'],n,'detached')) for i,n in enumerate(groups)}
        cfg.save()
        shelf=StickerShelf(folder)
        for index,(category,color,meaning) in enumerate((('开心','#F6B18C','开心回应'),('回应','#B5D4E6','表示收到'),('安慰','#C4DBBB','温柔安慰'))*2):
            art=Image.new('RGB',(160,160),'#F5F2EB');draw=ImageDraw.Draw(art)
            draw.ellipse((15,15,145,145),fill=color)
            for x in (57,103):draw.ellipse((x-4,58,x+4,66),fill='#1B3044')
            draw.arc((48,60,112,113),0,180,fill='#1B3044',width=5)
            draw.rectangle((70,130,90+index,136),fill='#1B3044')
            path=folder/f'sample-{index}.png';art.save(path)
            shelf.commit(inspect_picture(path),dict(category=category,meaning=meaning,tags=['日常',category],source='原创合成演示'))
        with patch('xiaomao.gui.ControlClient',DemoClient),patch('xiaomao.workspace_ui.ASSETS',brand):
            window=tk.Tk();panel=Panel(window,folder);window.geometry('1200x800+30+30')
            pump(window);panel.feedback.dismiss();shot(window,'workspace')
            panel.view.select('sessions');panel.feedback.dismiss();shot(window,'sessions')
            panel.view.select('stickers');pump(window,.8);panel.feedback.dismiss();shot(window,'stickers')
            stickers=panel.view.pages['stickers']
            stickers.edit_item(stickers.lib.items[0]);pump(window,.6);panel.feedback.dismiss();shot(window,'sticker-detail')
            stickers.back_to_library();pump(window)
            panel.view.select('settings');pump(window)
            page=panel.view.pages['settings'];page.save_button.invoke();pump(window,.8)
            assert '设置已保存' in panel.notice.get(),panel.notice.get()
            shot(window,'feedback')
            for size in ('930x620','1040x700','1200x800'):
                window.geometry(size);pump(window)
                assert page.save_button.winfo_rootx()+page.save_button.winfo_width()<=window.winfo_rootx()+window.winfo_width()
                assert panel.feedback.winfo_x()>=0
            panel.close()
    print('Captured 5 native previews with fictional data; responsive controls passed')


if __name__=='__main__':main()
