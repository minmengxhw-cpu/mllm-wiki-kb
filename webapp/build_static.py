# -*- coding: utf-8 -*-
"""
静态站构建：把 wiki/ + index 元数据渲染成单一加密内容包，输出到 docs/ 供 GitHub Pages 托管。
内容用口令 AES-256-GCM 加密（PBKDF2-HMAC-SHA256 派生密钥），浏览器端输入口令解密后渲染。
用法：  KB_PASSPHRASE=xxx python3 build_static.py
"""
import os, sys, json, base64, shutil, re, html

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "docs")
sys.path.insert(0, HERE)

try:
    import app as kbapp  # 复用载入/渲染逻辑
except ModuleNotFoundError:
    kbapp = None

PBKDF2_ITERS = 200000
LOCAL_PATH_RE = re.compile(r"/Users/cheer/Documents/mllm-wiki-kb/data/raw/([^<\s\"']+)")
CATEGORY_ORDER = [
    "盟史", "人物", "事件", "参政议政", "主题教育",
    "思想宣传", "传统教育基地", "文稿素材", "研究助手",
]
CATEGORY_DESC = {
    "盟史": "民盟与上海民盟组织发展沿革",
    "人物": "盟史核心人物与盟员风采",
    "事件": "重要会议、活动与历史节点",
    "参政议政": "履职素材与政策建议",
    "主题教育": "主题教育与思想建设",
    "思想宣传": "宣传文稿与思想引领",
    "传统教育基地": "传统教育与盟史教育基地",
    "文稿素材": "公众号写作范式与素材",
    "研究助手": "研究档案与系统说明",
}


def sanitize_public_string(value: str) -> str:
    value = LOCAL_PATH_RE.sub(r"[本地原文路径已脱敏]/\1", value)
    value = value.replace("/Users/cheer/Documents/mllm-wiki-kb", "[本地知识库路径已脱敏]")
    value = value.replace("/Users/cheer", "[本机用户目录已脱敏]")
    return value


def sanitize_public_content(value):
    if isinstance(value, dict):
        return {k: sanitize_public_content(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_public_content(v) for v in value]
    if isinstance(value, str):
        return sanitize_public_string(value)
    return value


def basic_frontmatter(text):
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        value = raw.strip().strip("\"'")
        if value.lower() in ("true", "false"):
            meta[key.strip()] = value.lower() == "true"
        elif value.startswith("[") and value.endswith("]"):
            meta[key.strip()] = [x.strip().strip("\"'") for x in value[1:-1].split(",") if x.strip()]
        else:
            meta[key.strip()] = value
    body = text[end + 4:].lstrip()
    return meta, body


def basic_render_md(text):
    lines = []
    in_list = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if in_list:
                lines.append("</ul>")
                in_list = False
            continue
        if line.startswith("#"):
            if in_list:
                lines.append("</ul>")
                in_list = False
            level = min(len(line) - len(line.lstrip("#")), 6)
            title = line[level:].strip()
            lines.append(f"<h{level}>{html.escape(title)}</h{level}>")
        elif line.startswith("- "):
            if not in_list:
                lines.append("<ul>")
                in_list = True
            lines.append(f"<li>{html.escape(line[2:])}</li>")
        else:
            if in_list:
                lines.append("</ul>")
                in_list = False
            lines.append(f"<p>{html.escape(line)}</p>")
    if in_list:
        lines.append("</ul>")
    return "\n".join(lines)


def fallback_load_pages():
    pages = {}
    wiki_dir = os.path.join(ROOT, "wiki")
    for dirpath, _dirs, files in os.walk(wiki_dir):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), wiki_dir)
            if rel in ("index.md", "log.md"):
                continue
            try:
                with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                    raw = f.read()
            except Exception:
                continue
            meta, body = basic_frontmatter(raw)
            body = body.replace("<!-- KB-GENERATED:START -->", "")
            body = body.replace("<!-- KB-GENERATED:END -->", "").strip()
            top = rel.split(os.sep)[0]
            slug = rel[:-3].replace(os.sep, "/")
            pages[slug] = {
                "slug": slug,
                "category": top if top in CATEGORY_DESC else "研究助手",
                "subdir": "/".join(rel.split(os.sep)[1:-1]),
                "title": (meta.get("title") or os.path.splitext(fn)[0]).strip(),
                "body_md": body,
                "tags": meta.get("tags") or [],
                "confidence": meta.get("confidence") or "",
                "needs_review": bool(meta.get("needs_review")),
                "source_count": meta.get("source_count") or 0,
                "last_compiled": (meta.get("last_compiled_at") or "")[:10],
            }
    return pages


def load_jsonl(path):
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def fallback_load_entities():
    edir = os.path.join(ROOT, "index", "entities")
    return {kind: load_jsonl(os.path.join(edir, kind + ".jsonl")) for kind in ("persons", "orgs", "events", "places")}


def build_content():
    if kbapp:
        pages = kbapp.load_pages()
        ents = kbapp.load_jsonl_entities()
        formus = kbapp.load_formulations()
        render_md = kbapp.render_md
        category_order = kbapp.CATEGORY_ORDER
        category_desc = kbapp.CATEGORY_DESC
    else:
        pages = fallback_load_pages()
        ents = fallback_load_entities()
        formus = load_jsonl(os.path.join(ROOT, "index", "formulations.jsonl"))
        render_md = basic_render_md
        category_order = CATEGORY_ORDER
        category_desc = CATEGORY_DESC

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
            "html": render_md(p["body_md"]),
        }
    # 六维度官方框架（《民主党派工作综合调研框架》）→ 现有 wiki 分类映射
    DIM_ORDER = ["参政议政", "组织建设", "思想建设", "社会服务", "理论研究", "党派历史研究"]
    DIM_DESC = {
        "参政议政": "履职建言、社情民意、提案与民主监督",
        "组织建设": "组织发展、领导班子与后备干部、机关建设",
        "思想建设": "政治交接、主题教育、思想引领与宣传",
        "社会服务": "智力支边、教育帮扶、烛光行动等品牌项目",
        "理论研究": "统一战线学、多党合作与参政党建设理论",
        "党派历史研究": "盟史、人物、事件与传统教育基地",
    }
    CAT_TO_DIM = {
        "参政议政": "参政议政",
        "思想宣传": "思想建设", "主题教育": "思想建设", "文稿素材": "思想建设",
        "盟史": "党派历史研究", "人物": "党派历史研究", "事件": "党派历史研究",
        "传统教育基地": "党派历史研究", "研究助手": "党派历史研究",
    }
    for p in out_pages.values():
        p["dimension"] = CAT_TO_DIM.get(p["category"], "党派历史研究")
    # 维度 → 分类 → 计数
    dims = []
    for dn in DIM_ORDER:
        cats = {}
        for p in out_pages.values():
            if p["dimension"] == dn:
                cats[p["category"]] = cats.get(p["category"], 0) + 1
        dims.append({"name": dn, "desc": DIM_DESC[dn], "count": sum(cats.values()), "cats": cats})
    content = {
        "pages": out_pages,
        "category_order": category_order,
        "category_desc": category_desc,
        "dimension_order": DIM_ORDER,
        "dimensions": dims,
        "entities": ents,
        "formulations": formus,
        "generated_pages": len(out_pages),
    }
    return content


def encrypt(plaintext: bytes, passphrase: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes

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
    if public:
        content = sanitize_public_content(content)
        content["public_mode"] = True
        content["sanitized"] = True
    else:
        content["public_mode"] = False
        content["sanitized"] = False
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
