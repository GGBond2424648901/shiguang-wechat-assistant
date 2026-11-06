"""Recover chat pane geometry from complete WeChat separator lines.

This only adjusts regions inside the already-bound window. A partial or
ambiguous layout keeps the explicit calibration, never guessed coordinates.
"""
from dataclasses import replace
import numpy as np


def idle_caption_point(image,scale=1.0):
    """Blank center of Qt's custom caption, excluding pin/window buttons."""
    if not hasattr(image,'size') or not .75<=scale<=3:return None
    w,h=image.size
    if w<360*scale or h<100*scale:return None
    x,y=w//2,round(16*scale)
    if not 70*scale<x<w-150*scale:return None
    pad=round(12*scale);dy=round(5*scale)
    patch=np.asarray(image.crop((x-pad,y-dy,x+pad+1,y+dy+1)).convert('RGB')).astype(np.int16)
    if patch.size and patch.min()>=190 and np.ptp(patch)<6:return x,y
    return None


def default_calibration_regions(kind='main'):
    """Starting points for a manual edit, in whole-window coordinates."""
    if kind=='main':
        return {'header':[.287,.021,.70,.073], 'messages':[.279,.128,.995,.763],
                'input':[.288,.817,.98,.932], 'send':[.90,.944,.984,.985]}
    if kind=='detached':
        return {'header':[.025,.018,.72,.09], 'messages':[.006,.11,.992,.70],
                'input':[.025,.79,.97,.90], 'send':[.79,.92,.97,.98]}
    raise ValueError('未知的微信窗口类型')


def adapt_calibration(image, binding, scale=1.0):
    if getattr(binding,'manual_regions',False):return binding
    if image.size!=(binding.width,binding.height):return binding
    if not .75<=scale<=3:return binding
    pixels=np.asarray(image.convert('RGB')).astype(np.int16)
    height,width=pixels.shape[:2]
    gray=pixels.mean(2);neutral=np.ptp(pixels,axis=2)<6
    # The selected recent-chat row on the LEFT is darker than the divider.
    # Detect its continuous contrast with the chat pane on the RIGHT; still
    # require one unique full-height boundary plus both horizontal separators.
    detached=getattr(binding,'window_kind','main')=='detached'
    if detached:
        divider=0
    else:
        vertical=(gray[:,1:-1]<gray[:,2:]-2)&neutral[:,1:-1]
        coverage=vertical[round(height*.12):round(height*.94)].mean(0)
        columns=[x for x in range(round(width*.12),round(width*.45)) if coverage[x-1]>.9]
        if len(columns)!=1:return binding
        divider=columns[0]
    horizontal=(gray[1:-1,:]<gray[:-2,:]-2)&(gray[1:-1,:]<gray[2:,:]-2)&neutral[1:-1,:]
    # At fractional DPI WeChat draws some dividers across two adjacent rows.
    upper,lower=gray[1:-2],gray[2:-1]
    horizontal[:-1]|=((upper<gray[:-3]-2)&(lower<gray[3:]-2)&(abs(upper-lower)<2)
                       &neutral[1:-2]&neutral[2:-1])
    line_start=round(12*scale) if detached else divider+2
    coverage=horizontal[:,line_start:round(width*.94)].mean(1)
    # A clipped chat bubble can touch the row immediately BELOW the header.
    # The separator itself must remain complete, uniform and neutral; its
    # upper boundary still needs full contrast. Do not relax toolbar checks.
    band=pixels[:,line_start:round(width*.94)]
    uniform=np.ptp(band,axis=(1,2))<3
    shade=band.mean(axis=(1,2))
    above=((gray[1:-1]<gray[:-2]-2)&neutral[1:-1])[:,line_start:round(width*.94)].mean(1)
    headers=[y for y in range(round(40*scale),round(height*.25))
             if coverage[y-1]>.98 or (uniform[y] and 190<=shade[y]<=225
                                      and above[y-1]>.98 and coverage[y-1]>.5)]
    toolbars=[y for y in range(round(height*.55),round(height*.94)) if coverage[y-1]>.98]
    if len(headers)!=1 or len(toolbars)!=1:return binding
    header,toolbar=headers[0],toolbars[0]
    if header>=toolbar or height-toolbar<140*scale:return binding
    # Header ends before the optional pinned-message strip. Input starts below
    # the toolbar; its lower edge excludes the Send button/footer.
    rectangles={
        'header':(divider+12*scale,18*scale,min(width-180*scale,width*.85),
                  min(header-5*scale,(96 if detached else 64)*scale)),
        'messages':(divider+2,header+2,width-10*scale,toolbar-3*scale),
        'input':(divider+12*scale,toolbar+50*scale,width-25*scale,height-62*scale),
        'send':(width-120*scale,height-57*scale,width-25*scale,height-24*scale),
    }
    regions={name:[x1/width,y1/height,x2/width,y2/height] for name,(x1,y1,x2,y2) in rectangles.items()}
    result=replace(binding,regions=regions)
    try:result.validate()
    except ValueError:return binding
    return result
