# -*- coding: utf-8 -*-
"""gov.cn 制度文献抓取器：静态HTML，提取正文(UCAP-CONTENT/zoom/TRS兜底)，按纪律打标存盘。"""
import os,re,html,time,urllib.request,datetime
OUT="/home/zq/work/mllm-wiki-kb/corpus_crawl/制度文献"
UA={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
TODAY=datetime.date(2026,6,13).isoformat()
DOCS=[
 ("中国新型政党制度（白皮书）","https://www.gov.cn/zhengce/2021-06/25/content_5620794.htm","制度文件·白皮书","新时代","官方原发"),
 ("中国的政党制度（白皮书）","https://www.gov.cn/zhengce/2007-11/15/content_2615762.htm","制度文件·白皮书","改革开放","官方原发"),
 ("中国共产党统一战线工作条例","https://www.gov.cn/zhengce/2021-01/05/content_5577289.htm","制度文件·党内法规","新时代","官方原发"),
]
def fetch(url):
    return urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=30).read().decode('utf-8','ignore')
def extract(h,url):
    t=re.search(r'<title>(.*?)</title>',h,re.S); title=html.unescape(t.group(1)).split('_')[0].strip() if t else ''
    m=re.search(r'/(\d{4})-(\d{2})/(\d{2})/',url); pub=f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ''
    i=h.find('UCAP-CONTENT')
    if i<0:
        mm=re.search(r'<(?:div|td)[^>]*(?:id|class)="?(?:zoom|TRS_Editor|content|conTxt|article)"?[^>]*>',h,re.I)
        i=mm.start() if mm else -1
    if i>0: i=h.find('>',i)+1
    seg=h[i:i+300000] if i>0 else h
    seg=re.sub(r'<(script|style)[^>]*>.*?</\1>','',seg,flags=re.S|re.I)
    seg=re.sub(r'</p>','\n',seg,flags=re.I); seg=re.sub(r'<br\s*/?>','\n',seg,flags=re.I)
    seg=re.sub(r'<[^>]+>','',seg); txt=html.unescape(seg)
    txt=re.sub(r'[ \t]+',' ',txt); txt=re.sub(r'\n[ \t]+','\n',txt); txt=re.sub(r'\n{3,}','\n\n',txt).strip()
    drop={'字号：','默认','大','超大','|','打印','分享','扫一扫在手机打开当前页',''}
    lines=[l for l in txt.split('\n') if l.strip() not in drop]
    return title, pub, '\n'.join(lines).strip()
def main():
    os.makedirs(OUT,exist_ok=True); ok=0
    for title,url,layer,period,auth in DOCS:
        try:
            _,pub,body=extract(fetch(url),url)
            if len(body)<500: print(f"⚠ {title}: 正文仅 {len(body)} 字，疑似提取失败"); continue
            name=re.sub(r'[\\/:*?"<>|]','_',title)+'.md'
            fm=(f"---\ntitle: \"{title}\"\nsource_url: {url}\nsource_site: 中国政府网 gov.cn\n"
                f"publish_date: {pub}\nfetched_at: {TODAY}\ndoc_layer: {layer}\nperiod: {period}\nauthority: {auth}\n---\n\n")
            open(os.path.join(OUT,name),'w',encoding='utf-8').write(fm+body+'\n')
            ok+=1; print(f"✓ {title}  正文 {len(body)} 字  发布 {pub}")
            time.sleep(1)
        except Exception as e:
            print(f"✗ {title}: {e}")
    print(f"完成 {ok}/{len(DOCS)} → {OUT}")
main()
