"""Recover chat pane geometry from complete WeChat separator lines.

This only adjusts regions inside the already-bound window. A partial or
ambiguous layout keeps the explicit calibration, never guessed coordinates.
"""
from dataclasses import replace
import numpy as np


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
    headers=[y for y in range(round(40*scale),round(height*.25)) if coverage[y-1]>.98]
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
