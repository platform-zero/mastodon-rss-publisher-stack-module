import glob, hashlib, io, json, os, re, sys
from collections import defaultdict
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
out = os.path.join(root, 'mastodon-rss-publisher-stack-module/stack.config/mastodon-rss/assets')
feeds = []
for p in glob.glob(root + '/mastodon-rss-*-stack-module/stack.config/mastodon-rss/feeds.d/*.json'):
    feeds.extend(json.load(open(p))['feeds'])
feeds.sort(key=lambda f: f['account'])
SITES = {
 'NPR':'https://www.npr.org/', 'BBC News':'https://www.bbc.com/news',
 'The New York Times':'https://www.nytimes.com/', 'ABC News':'https://www.abc.net.au/news/',
 'PBS NewsHour':'https://www.pbs.org/newshour/', 'The Guardian':'https://www.theguardian.com/',
 'Science':'https://www.science.org/', 'Nature':'https://www.nature.com/',
 'The Washington Post':'https://www.washingtonpost.com/', 'Le Monde':'https://www.lemonde.fr/',
 'Ars Technica':'https://arstechnica.com/', 'CGTN':'https://www.cgtn.com/',
}
class Icons(HTMLParser):
 def __init__(self): super().__init__(); self.icons=[]
 def handle_starttag(self,tag,attrs):
  if tag!='link': return
  a=dict(attrs); rel=a.get('rel','').lower(); href=a.get('href')
  if href and 'icon' in rel:
   size=a.get('sizes','')
   score=(1000 if 'apple-touch-icon' in rel else 500 if 'icon' in rel else 0)
   n=re.search(r'(\d+)x\d+',size)
   if n: score+=min(int(n.group(1)),512)
   if href.lower().endswith('.svg'): score-=100
   self.icons.append((score,href))

def get_icon(source, feed):
 url=SITES.get(source)
 if not url:
  u=urlparse(feed['url']); url=f'{u.scheme}://{u.netloc}/'
 try:
  r=requests.get(url,timeout=20,headers={'User-Agent':'Mozilla/5.0'},allow_redirects=True)
  r.raise_for_status()
  parser=Icons(); parser.feed(r.text[:2000000]); icons=sorted(parser.icons,reverse=True)
  icons += [(0,'/favicon.ico')]
  for _,href in icons:
   try:
    icon_url=urljoin(r.url,href)
    q=requests.get(icon_url,timeout=15,headers={'User-Agent':'Mozilla/5.0'})
    q.raise_for_status()
    if len(q.content)>2000000: continue
    if q.content.lstrip().startswith(b'<svg') or 'svg' in q.headers.get('Content-Type',''):
     import cairosvg
     raw=cairosvg.svg2png(bytestring=q.content,output_width=360,output_height=360)
    else: raw=q.content
    im=Image.open(io.BytesIO(raw)); im.seek(0); im=im.convert('RGBA')
    if min(im.size)<32: continue
    # Trim empty margins embedded in some official icons before fitting the plate.
    white=Image.new('RGBA',im.size,'white')
    white.alpha_composite(im)
    rgb=white.convert('RGB')
    mask=rgb.point(lambda x: 255 if x<225 else 0).convert('L')
    box=mask.getbbox()
    if box and (box[2]-box[0])>=8 and (box[3]-box[1])>=8:
     im=im.crop(box)
    return im,icon_url
   except Exception: continue
 except Exception as e: print('SITE ERROR',source,url,str(e)[:80])
 return None,None

font='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
regular='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
def fit(draw, text, maxwidth, maxsize=37):
 for size in range(maxsize,14,-1):
  f=ImageFont.truetype(font,size)
  if draw.textbbox((0,0),text,font=f)[2]<=maxwidth: return f
 return ImageFont.truetype(font,15)

def render(feed,icon):
 user=feed['account']; source=feed['source']; label=feed['display_name']
 hue=int(hashlib.sha256(source.encode()).hexdigest()[:2],16)
 # Muted navy field and a publisher-colored top accent distinguish entries.
 bg=(18+(hue%16),31+(hue%22),49+(hue%31))
 im=Image.new('RGB',(512,512),bg); d=ImageDraw.Draw(im)
 d.rounded_rectangle((20,20,492,492),radius=38,fill=(250,250,249))
 d.rounded_rectangle((20,20,492,396),radius=38,fill=(239,242,245))
 if icon:
  scale=min(294/icon.width,294/icon.height)
  icon=icon.resize((max(1,round(icon.width*scale)),max(1,round(icon.height*scale))),Image.Resampling.LANCZOS)
  # White plate keeps publisher artwork legible, including transparent icons.
  plate=Image.new('RGBA',(324,324),'white')
  plate.alpha_composite(icon,((324-icon.width)//2,(324-icon.height)//2))
  im.paste(plate.convert('RGB'),(94,43))
 else:
  # Publishers without accessible icons receive a source-name mark.
  short=source.upper()
  f=fit(d,short,380,54)
  box=d.textbbox((0,0),short,font=f)
  d.text(((512-(box[2]-box[0]))//2,173),short,font=f,fill=bg)
 # Display name gives accounts using the same publisher separate, visible identity.
 name=label.upper()
 f=fit(d,name,440,38)
 box=d.textbbox((0,0),name,font=f)
 d.text(((512-(box[2]-box[0]))//2,421),name,font=f,fill=(19,31,47))
 # Small persistent bot watermark over the main field.
 d.rounded_rectangle((405,344,476,383),radius=11,fill=(18,31,49))
 bf=ImageFont.truetype(font,20)
 d.text((417,351),'bot',font=bf,fill='white')
 return im

os.makedirs(out,exist_ok=True)
icons={}
for feed in feeds:
 source=feed['source']
 if source not in icons:
  icons[source]=get_icon(source,feed)
  print(source,icons[source][1] or 'fallback',flush=True)
manifest=[]
for feed in feeds:
 im=render(feed,icons[feed['source']][0])
 data=io.BytesIO(); im.save(data,format='PNG',optimize=True)
 digest=hashlib.sha256(data.getvalue()).hexdigest()[:12]
 name=f"{feed['account']}-{digest}.png"
 with open(os.path.join(out,name),'wb') as f: f.write(data.getvalue())
 manifest.append({'account':feed['account'],'source':feed['source'],'avatar':name,'publisher_icon':icons[feed['source']][1]})
observer={'account':'rss_observer','source':'RSS Timeline Observer','display_name':'RSS Observer'}
im=render(observer,None); data=io.BytesIO(); im.save(data,format='PNG',optimize=True)
name=f"rss_observer-{hashlib.sha256(data.getvalue()).hexdigest()[:12]}.png"
with open(os.path.join(out,name),'wb') as f: f.write(data.getvalue())
manifest.append({'account':'rss_observer','source':'RSS Timeline Observer','avatar':name,'publisher_icon':None})
for old in glob.glob(os.path.join(out,'rss_*.png')):
 if os.path.basename(old) not in {x['avatar'] for x in manifest}: os.unlink(old)
with open(os.path.join(out,'sources.json'),'w') as f: json.dump(manifest,f,indent=2); f.write('\n')
print('AVATARS',len(manifest),'OFFICIAL ICONS',sum(x['publisher_icon'] is not None for x in manifest),flush=True)
