# -*- coding: utf-8 -*-
"""盟讯机关刊物批量下载(2018-2021)：慢服务器续传+pypdf提取+打标入库。"""
import os,re,datetime,urllib.request,pypdf
OUT="/home/zq/work/mllm-wiki-kb/corpus_crawl/盟讯"; LOG="/tmp/mengxun.log"
UA={'User-Agent':'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X)'}
TODAY=datetime.date(2026,6,13).isoformat()
os.makedirs(OUT,exist_ok=True)
def log(m): open(LOG,"a").write(f"{datetime.datetime.now().strftime('%H:%M:%S')} {m}\n")
codes=[f"{y}{m:02d}" for y in range(2018,2022) for m in range(1,13)]
log(f"盟讯批量：{len(codes)} 期候选")
ok=0
for code in codes:
    out=os.path.join(OUT,f"盟讯{code}_总期.md")
    if os.path.exists(out): ok+=1; continue
    url=f"https://www.mmzy.org.cn/upload/pdf/{code}mx.pdf"; tmp=f"/tmp/mx_{code}.pdf"
    try:
        for _ in range(40):
            data=open(tmp,'rb').read() if os.path.exists(tmp) else b''
            try:
                req=urllib.request.Request(url,headers={**UA,'Range':f'bytes={len(data)}-'})
                data+=urllib.request.urlopen(req,timeout=110).read()
                open(tmp,'wb').write(data)
            except Exception: pass
            if data[-2048:].find(b'%%EOF')>=0: break
        if len(data)<50000: log(f"{code} 太小({len(data)}B)跳过"); os.remove(tmp); continue
        r=pypdf.PdfReader(tmp,strict=False); txt='\n'.join((p.extract_text() or '') for p in r.pages)
        if len(txt)<800: log(f"{code} 文字少跳过"); continue
        yr,no=code[:4],int(code[4:6])
        fm=(f'---\ntitle: "盟讯 {yr}年第{no}期"\nsource_url: {url}\nsource_site: 民盟中央 mmzy.org.cn\n'
            f'fetched_at: {TODAY}\ndoc_layer: 机关刊物·盟讯\nperiod: 新时代\nauthority: 官方原发\n---\n\n')
        open(out,'w',encoding='utf-8').write(fm+txt+'\n'); ok+=1
        log(f"✓ 盟讯{yr}年第{no}期 {len(txt)}字 (累计{ok})")
        try: os.remove(tmp)
        except: pass
    except Exception as e: log(f"✗ {code}: {e}")
log(f"完成：{ok} 期入库")
