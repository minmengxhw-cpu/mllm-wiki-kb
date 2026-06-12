# -*- coding: utf-8 -*-
"""
静态站构建：把 wiki/ + index 元数据渲染成单一加密内容包，输出到 docs/ 供 GitHub Pages 托管。
内容用口令 AES-256-GCM 加密（PBKDF2-HMAC-SHA256 派生密钥），浏览器端输入口令解密后渲染。
用法：  KB_PASSPHRASE=xxx python3 build_static.py
"""
import os, sys, json, base64, shutil
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "docs")
sys.path.insert(0, HERE)

import app as kbapp  # 复用载入/渲染逻辑

PBKDF2_ITERS = 200000


def build_content():
    pages = kbapp.load_pages()
    ents = kbapp.load_jsonl_entities()
    formus = kbapp.load_formulations()

    out_pages = {}
    for slug, p in pages.items():
        out_pages[slug] = {
            "slug": slug,
            "title": p["title"],
            "category": p["category"],
            "subdir": p["subdir"],
            "tags": p["tags"],
            "confidence": p["confidence"],
            "needs_review": p["needs_review"],
            "source_count": p["source_count"],
            "last_compiled": p["last_compiled"],
            "html": kbapp.render_md(p["body_md"]),
        }
    content = {
        "pages": out_pages,
        "category_order": kbapp.CATEGORY_ORDER,
        "category_desc": kbapp.CATEGORY_DESC,
        "entities": ents,
        "formulations": formus,
        "generated_pages": len(out_pages),
    }
    return content


def encrypt(plaintext: bytes, passphrase: str) -> str:
    salt = os.urandom(16)
    iv = os.urandom(12)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PBKDF2_ITERS)
    key = kdf.derive(passphrase.encode())
    ct = AESGCM(key).encrypt(iv, plaintext, None)  # 含 16B tag
    blob = salt + iv + ct
    return base64.b64encode(blob).decode()


def main():
    public = os.environ.get("KB_PUBLIC") == "1"
    pw = os.environ.get("KB_PASSPHRASE")
    if not public and not pw:
        print("ERROR: 加密模式需设置环境变量 KB_PASSPHRASE（或用 KB_PUBLIC=1 走公开明文模式）", file=sys.stderr)
        sys.exit(1)

    content = build_content()
    raw = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    os.makedirs(DOCS, exist_ok=True)

    if public:
        # 公开模式：明文内容包，无需口令
        with open(os.path.join(DOCS, "content.json"), "w", encoding="utf-8") as f:
            f.write(raw.decode("utf-8"))
        # 清理旧加密产物
        for fn in ("content.enc", "meta.json"):
            p = os.path.join(DOCS, fn)
            if os.path.exists(p):
                os.remove(p)
        mode = "公开明文"
        size_kb = len(raw) / 1024
    else:
        enc = encrypt(raw, pw)
        with open(os.path.join(DOCS, "content.enc"), "w", encoding="utf-8") as f:
            f.write(enc)
        with open(os.path.join(DOCS, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"iters": PBKDF2_ITERS, "pages": content["generated_pages"]}, f)
        mode = "口令加密"
        size_kb = len(enc) / 1024

    open(os.path.join(DOCS, ".nojekyll"), "w").close()
    shutil.copy(os.path.join(HERE, "static", "style.css"), os.path.join(DOCS, "style.css"))
    print(f"✓ 构建完成（{mode}）：{content['generated_pages']} 页，{size_kb:.0f} KB → docs/")


if __name__ == "__main__":
    main()
