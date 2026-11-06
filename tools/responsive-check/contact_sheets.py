import sys,glob
from PIL import Image,ImageDraw
tag=sys.argv[1]; views=sys.argv[2].split(','); sizes=sys.argv[3].split(',')
H=int(sys.argv[4]) if len(sys.argv)>4 else 560
for v in views:
    ims=[]
    for s in sizes:
        try: im=Image.open(f'shots/{tag}-{v}-{s}.png').convert('RGB')
        except Exception: continue
        sc=H/im.height; im=im.resize((max(1,int(im.width*sc)),H))
        d=ImageDraw.Draw(im); d.rectangle([0,0,70,14],fill=(0,0,0)); d.text((2,2),s,fill=(255,255,0))
        ims.append(im)
    if not ims: continue
    W=sum(i.width for i in ims)+6*(len(ims)-1)
    sh=Image.new('RGB',(W,H),(255,0,255)); x=0
    for i in ims: sh.paste(i,(x,0)); x+=i.width+6
    sh.save(f'shots/sheet-{tag}-{v}-{sys.argv[5]}.png'); print(v,sh.size)
