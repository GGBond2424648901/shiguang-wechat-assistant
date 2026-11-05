"""限时的公开窗口绘图捕获；子进程不接触聊天存储或模型。"""
import ctypes
import multiprocessing as mp
from PIL import Image


class CaptureError(RuntimeError):
    pass


def _render(hwnd,width,height,pixels,connection):
    import win32gui
    import win32ui
    source=src=dst=bitmap=old=None
    ok=False
    try:
        from ctypes import wintypes as W
        user=ctypes.WinDLL('user32',use_last_error=True)
        user.PrintWindow.argtypes=[W.HWND,W.HDC,W.UINT]
        user.PrintWindow.restype=W.BOOL
        source=win32gui.GetWindowDC(hwnd)
        src=win32ui.CreateDCFromHandle(source);dst=src.CreateCompatibleDC()
        bitmap=win32ui.CreateBitmap();bitmap.CreateCompatibleBitmap(src,width,height)
        old=dst.SelectObject(bitmap)
        if user.PrintWindow(hwnd,dst.GetSafeHdc(),2):
            data=bitmap.GetBitmapBits(True)
            if len(data)==width*height*4:
                memoryview(pixels).cast('B')[:]=data
                ok=True
    except Exception:
        pass
    finally:
        if old is not None and dst is not None:dst.SelectObject(old)
        if bitmap is not None:win32gui.DeleteObject(bitmap.GetHandle())
        if dst is not None:dst.DeleteDC()
        if src is not None:src.DeleteDC()
        if source is not None:win32gui.ReleaseDC(hwnd,source)
        try:connection.send(ok)
        finally:connection.close()


def capture_window(hwnd:int,width:int,height:int,timeout=4.0):
    if not (0<width<=10000 and 0<height<=10000 and width*height<=30_000_000):
        raise CaptureError('窗口范围无效')
    context=mp.get_context('spawn')
    pixels=context.RawArray('B',width*height*4)
    receiver,sender=context.Pipe(duplex=False)
    process=context.Process(target=_render,args=(hwnd,width,height,pixels,sender),daemon=True)
    try:
        process.start();sender.close()
        if not receiver.poll(timeout) or not receiver.recv():
            raise CaptureError('后台窗口画面暂不可用')
        image=Image.frombuffer('RGB',(width,height),bytes(pixels),'raw','BGRX',0,1)
        low,high=image.convert('L').getextrema()
        if high-low<15:raise CaptureError('后台窗口返回空白画面')
        return image
    except (EOFError,OSError) as error:
        raise CaptureError('后台窗口捕获失败') from None
    finally:
        receiver.close();sender.close()
        if process.pid is not None:
            process.join(.1)
            if process.is_alive():process.terminate();process.join(1)
            process.close()
