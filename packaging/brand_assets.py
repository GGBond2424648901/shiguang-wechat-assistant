"""Reproducible, original neutral artwork for the public application."""
from pathlib import Path
from PIL import Image, ImageDraw

NAMES=('app.png','app.ico','persona-avatar.png')


def create_assets(destination: Path):
    destination.mkdir(parents=True,exist_ok=True)
    art=Image.new('RGBA',(512,512),(0,0,0,0));pen=ImageDraw.Draw(art)
    pen.rounded_rectangle((20,20,492,492),radius=115,fill='#1B3044')
    pen.polygon([(115,220),(118,100),(214,155),(298,155),(394,100),(397,220)],fill='#F6B18C')
    pen.rounded_rectangle((105,155,407,382),radius=96,fill='#F6B18C')
    for x in (198,314):pen.ellipse((x-13,239,x+13,265),fill='#1B3044')
    pen.line([(240,294),(256,304),(272,294)],fill='#1B3044',width=12,joint='curve')
    pen.arc((210,286,257,329),0,90,fill='#1B3044',width=8)
    pen.arc((255,286,302,329),90,180,fill='#1B3044',width=8)
    art.save(destination/'app.png')
    art.save(destination/'app.ico',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    art.save(destination/'persona-avatar.png')
