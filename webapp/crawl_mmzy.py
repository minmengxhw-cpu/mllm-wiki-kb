# -*- coding: utf-8 -*-
"""民盟中央 mmzy.org.cn 抓取：移动版静态页(要闻/概况) + /upload/pdf 盟讯机关刊物。"""
import os,re,html,time,datetime,urllib.request
import pypdf
OUT="/home/zq/work/mllm-wiki-kb/corpus_crawl"
UA={'User-Agent':'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15'}
TODAY=datetime.date(2026,6,13).isoformat()
def get(url,binary=False,timeout=120):
    r=urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=timeout)
    return r.read() if binary else r.read().decode('utf-8','ignore')
def clean(h):
    b=re.sub(r'<(script|style)[^>]*>.*?</\1>','',h,flags=re.S|re.I)
    b=re.sub(r'</p>','\n',b,flags=re.I); b=re.sub(r'<br\s*/?>','\n',b,flags=re.I)
    b=re.sub(r'<[^>]+>','',b); b=html.unescape(b)
    return re.sub(r'\n{3,}','\n\n',re.sub(r'[ \t]+',' ',b)).strip()
def save(sub,name,fm,body):
    d=os.path.join(OUT,sub); os.makedirs(d,exist_ok=True)
    open(os.path.join(d,re.sub(r'[\\/:*?"<>|]','_',name)+'.md'),'w',encoding='utf-8').write(fm+body+'\n')

def pull_mengxun(issues):
    """盟讯机关刊物 PDF：/upload/pdf/YYYYNNmx.pdf"""
    ok=0
    for code in issues:
        url=f"https://www.mmzy.org.cn/upload/pdf/{code}.pdf"; tmp=f"/tmp/{code}.pdf"
        try:
            # 慢服务器：续传到 EOF
            for _ in range(30):
                data=b''
                if os.path.exists(tmp): data=open(tmp,'rb').read()
                req=urllib.request.Request(url,headers={**UA,'Range':f'bytes={len(data)}-'})
                try: data+=urllib.request.urlopen(req,timeout=110).read()
                except Exception: pass
                open(tmp,'wb').write(data)
                if data[-1024:].find(b'%%EOF')>=0: break
            r=pypdf.PdfReader(tmp,strict=False); txt='\n'.join((p.extract_text() or '') for p in r.pages)
            if len(txt)<800: print(f"⚠ {code} 文字少({len(txt)})，跳过"); continue
            yr,no=code[:4],int(code[4:6])
            fm=(f'---\ntitle: "盟讯 {yr}年第{no}期"\nsource_url: {url}\nsource_site: 民盟中央 mmzy.org.cn\n'
                f'fetched_at: {TODAY}\ndoc_layer: 机关刊物·盟讯\nperiodः 新时代\nauthority: 官方原发\n---\n\n')
            fm=fm.replace('periodः','period:')
            save("盟讯",f"盟讯{code}",fm,txt); ok+=1; print(f"✓ 盟讯 {yr}年第{no}期 {len(txt)}字")
        except Exception as e: print(f"✗ {code}: {e}")
    return ok

def pull_mobile_article(url,layer):
    h=get(url); t=re.search(r'<title>(.*?)</title>',h,re.S)
    title=html.unescape(t.group(1)).strip() if t else ''
    body=clean(h)
    return title,body

if __name__=="__main__":
    import sys
    # 仅处理已下好的 2018-01 盟讯（演示），批量另跑
    if os.path.exists('/tmp/201801mx.pdf') or os.path.exists('/tmp/mx.pdf'):
        src='/tmp/201801mx.pdf' if os.path.exists('/tmp/201801mx.pdf') else '/tmp/mx.pdf'
        r=pypdf.PdfReader(src,strict=False); txt='\n'.join((p.extract_text() or '') for p in r.pages)
        fm=('---\ntitle: "盟讯 2018年第1期（总第420期）"\n'
            'source_url: https://www.mmzy.org.cn/upload/pdf/201801mx.pdf\n'
            'source_site: 民盟中央 mmzy.org.cn\nfetched_at: '+TODAY+'\n'
            'doc_layer: 机关刊物·盟讯\nperiod: 新时代\nauthority: 官方原发\n---\n\n')
        save("盟讯","盟讯201801_总第420期",fm,txt); print(f"✓ 盟讯2018年第1期 入库 {len(txt)}字")
