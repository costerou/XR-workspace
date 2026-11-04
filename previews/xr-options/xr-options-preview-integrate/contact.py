from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import shutil
p=Path(__file__).parent
font=ImageFont.truetype(str(p/'xr-mono.ttf'),22)
canvas=Image.new('RGB',(1800,2200),'#101416');d=ImageDraw.Draw(canvas)
d.text((24,16),'XR WORKSPACE / COMBINED DESIGN REVIEW',font=font,fill='#dce5df')
d.text((24,52),'HTML MOCKUPS — not native screenshots • B3 + C2 + F1 recommended',font=font,fill='#bcd6a3')
im=Image.open(p/'contact-sheet.png');im.crop((0,0,1440,3200)).resize((850,1888)).save(p/'gallery-overview.png');canvas.paste(Image.open(p/'gallery-overview.png'),(20,110))
y=110
for name in ['button-contact-sheet.png','compact-contact-sheet.png','full-contact-sheet.png']:
 src=p/'assets'/name
 if src.exists():
  im=Image.open(src);im.thumbnail((850,740));d.text((910,y),name,font=font,fill='#dce5df');canvas.paste(im,(910,y+38));y+=im.height+70
canvas.save(p/'contact-sheet.png');shutil.copy2(p/'contact-sheet.png','/home/cody/.hermes/profiles/coder/previews/xr-options/contact-sheet.png')
