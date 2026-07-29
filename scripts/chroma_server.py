"""启动 ChromaDB HTTP Server，暴露 data/chroma/ 数据给外部可视化工具.

关键修复（共 3 个 ChromaDB 1.5.9 兼容性问题）：
  1) Settings.is_persistent 默认 False → 必须显式 Settings(is_persistent=True, persist_directory=...)
  2) chromadb-admin 1.x 的 Test Connection 请求 /api/v2/healthcheck → ChromaDB 原生没有，补路由
  3) HTTP 序列化 embedding 返回时调用 cast(Embedding, x).tolist()，x 是 Python list 时 AttributeError
     → 通过 _patch_chromadb_tolist.py 打补丁到 site-packages，在 4 个调用点加 isinstance 判断

备份：chromadb.server.fastapi.__init__.py 的补丁备份在同一目录 __init__.py.bak_tolist_patch

另外：内嵌一个轻量数据查看器 /viewer，绕过 chromadb-admin 的 Node.js 代理（它会截断大 response）。
"""

import time
from pathlib import Path
from typing import Any

# Studio 项目根（本脚本在 Studio/scripts/ → parent 是 scripts → parent.parent 是 Studio 根）
studio_dir = Path(__file__).parent.parent

# 数据目录（在 argparse 解析后在 main() 里根据 --persist-path 注入实际值，这里先占位为全局可变对象）
_CHROMA_DIR: Path | None = None


def _resolve_persist_path(cli_value: str | None) -> Path:
    """解析 --persist-path 参数：绝对路径直接用；相对路径按「当前 working directory」解析；不传则默认 Studio/data/chroma."""
    if cli_value:
        p = Path(cli_value).expanduser()
        if not p.is_absolute():
            # 允许用户用相对路径（相对于当前 shell cwd，而不是 studio_dir，避免跨项目混淆）
            p = Path.cwd() / p
        return p.resolve()
    # 默认：Studio/data/chroma
    return (studio_dir / "data" / "chroma").resolve()


import uvicorn
from chromadb.config import Settings
from chromadb.server.fastapi import FastAPI as ChromaFastAPI
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

# ⚠️ Settings + ChromaFastAPI 实例延迟到 main() 里构造：
#    因为要先解析 --persist-path 参数，才能知道 persist_directory 实际路径。
_chroma_settings: Settings | None = None
_chroma_server: ChromaFastAPI | None = None
chroma_app: FastAPI | None = (
    None  # 用 FastAPI 类型标注（实际是 chromadb FastAPI app 实例，也是 FastAPI 子类）
)


def _init_chroma_app(persist_path: Path):
    """在解析完 argparse 后，懒加载 ChromaDB 实例 + 挂载到外层 app."""
    global _chroma_settings, _chroma_server, chroma_app
    persist_path.mkdir(parents=True, exist_ok=True)

    # ⚠️ Settings.model_config.env_prefix == ""（空字符串），CHROMA_* env 无效，必须传 kwarg
    _chroma_settings = Settings(
        is_persistent=True,
        persist_directory=str(persist_path),
        anonymized_telemetry=False,
    )
    _chroma_server = ChromaFastAPI(_chroma_settings)
    chroma_app = _chroma_server.app()

    # 在外层 app 上挂载原生 Chroma app（其他路由已在前面 @app 注册，优先匹配）
    app.mount("/", chroma_app)


# ── 外层包装 app ──────────────────────────────────────────────
app = FastAPI(
    title="ChromaDB Server (AIGameWorld-Studio · 知识库)",
    description="ChromaDB PersistentClient HTTP server + health + data viewer endpoints",
    version="1.2.0",
)

# 给浏览器直接访问 8001 用的 CORS（配合 data viewer / 第三方 UI）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)


# ── 健康检查端点 ─────────────────────────────────────────────
@app.get("/", tags=["health"])
@app.get("/health", tags=["health"])
@app.get("/status", tags=["health"])
def root_health() -> dict:
    return {
        "status": "ok",
        "service": "chromadb",
        "persist_directory": str(_CHROMA_DIR),
        "nanosecond_heartbeat": time.time_ns(),
        "viewer": "/viewer",
    }


@app.get("/api/v2/healthcheck", tags=["health"])
def chroma_admin_healthcheck() -> dict:
    """chromadb-admin 1.x 的 Test Connection 专用端点（原生未提供，导致 404 锁死 Connect 按钮）."""
    return {
        "status": "ok",
        "service": "chromadb",
        "version": "compatible-with-v2-admin-ui",
        "nanosecond_heartbeat": time.time_ns(),
    }


# ═══════════════════════════════════════════════════════════════════════
# 内嵌轻量级数据查看器  /viewer  → 单文件 HTML + JS，直接 fetch API
#
# ⚠️ 实现注意：
#   ChromaFastAPI 的方法都需要 FastAPI Request 上下文，不能直接在另一个路由里同步调用
#   所以这里 viewer 的后端接口统一用 HTTP 打自己（{base_url}/api/v1），base_url 由 main() 动态注入
# ═══════════════════════════════════════════════════════════════════════
import threading

import requests as _requests

_LOCAL_BASE: str = "http://127.0.0.1:8001"  # 在 main() 里根据 --port/--host 重写
_V1_PRE = "/api/v1"


def _set_local_base(base_url: str) -> None:
    """main() 解析完端口/host 后调用：让 viewer 的 _http() 函数连到正确的端口."""
    global _LOCAL_BASE
    _LOCAL_BASE = base_url.rstrip("/")


def _http(method: str, path: str, **kwargs):
    """向自己所在的 chroma server 发请求（复用 v1 路由）."""
    url = _LOCAL_BASE + path
    kw = dict(timeout=60)
    kw.update(kwargs)
    resp = _requests.request(method, url, **kw)
    resp.raise_for_status()
    return resp.json()


def _api_list_collections() -> list[dict]:
    """返回所有 collection：name + count + uuid."""
    out: list[dict] = []
    raw = _http("GET", _V1_PRE + "/collections")
    # v1 GET /collections 返回 ChromaDB Collection model list 样 {"id":..., "name":...}
    for col in raw:
        cid = col["id"]
        try:
            cnt_resp = _http("GET", _V1_PRE + f"/collections/{cid}/count")
            cnt = int(cnt_resp.get("count", cnt_resp) if isinstance(cnt_resp, dict) else cnt_resp)
        except Exception:
            cnt = -1
        out.append(
            {"id": str(cid), "name": str(col.get("name", col.get("name"))), "count": int(cnt)}
        )
    out.sort(key=lambda x: (-x["count"], x["name"]))
    return out


def _api_get_records(name: str, limit: int, with_embeddings: bool) -> dict[str, Any]:
    """取某 collection 前 N 条 records（ids, documents, metadatas, embeddings）."""
    # 先用 name 拿到 collection（带 id）
    col = _http("GET", _V1_PRE + f"/collections/{name}")
    cid = col["id"]
    include = ["documents", "metadatas"]
    if with_embeddings:
        include.append("embeddings")
    result = _http(
        "POST",
        _V1_PRE + f"/collections/{cid}/get",
        json={"limit": int(limit), "include": include},
    )
    return dict(result) if not isinstance(result, dict) else result


@app.get("/_/collections", tags=["viewer"])
def viewer_list_collections():
    return JSONResponse({"collections": _api_list_collections()})


@app.get("/_/collections/{name}/records", tags=["viewer"])
def viewer_get_records(name: str, limit: int = 10, embeddings: bool = False):
    try:
        data = _api_get_records(name, limit=limit, with_embeddings=embeddings)
    except Exception as e:
        return JSONResponse({"error": type(e).__name__, "message": str(e)}, status_code=400)
    # 缩短 embedding 的展示（只返回前 5 个数字 + 总维度，节省带宽）
    if data.get("embeddings"):
        shortened = []
        for emb in data["embeddings"]:
            if emb is None:
                shortened.append(None)
            else:
                shortened.append(
                    {"dim": len(emb), "first_values": emb[:5], "last_values": emb[-3:]}
                )
        data["embeddings_preview"] = shortened
        del data["embeddings"]  # 不返回完整 embedding（太大），用 preview 代替
    return JSONResponse(data)


_VIEWER_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>AIGameWorld ChromaDB Data Viewer</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background:#0f172a; color:#e2e8f0; line-height:1.45 }
  header { padding: 18px 24px; border-bottom:1px solid #1e293b; background:#0b1220; position: sticky; top:0; z-index:10 }
  header h1 { margin:0; font-size: 18px }
  header .sub { opacity:.6; font-size: 12px; margin-top: 3px }
  main { padding: 20px 24px; display:grid; grid-template-columns: 320px 1fr; gap: 20px; max-width: 1600px; margin: 0 auto }
  @media(max-width: 900px){ main { grid-template-columns: 1fr } }
  .card { background:#111827; border:1px solid #1f2937; border-radius:10px; padding: 14px 16px }
  .stats { display:grid; grid-template-columns: repeat(3,1fr); gap:10px; margin-bottom:14px }
  .stat { padding:10px 12px; background:#0b1220; border-radius:8px; border:1px solid #1e293b }
  .stat b { display:block; font-size: 20px; color:#93c5fd }
  .stat span { font-size:11px; opacity:.6; text-transform: uppercase; letter-spacing: .5px }
  ul.cols { list-style:none; margin:0; padding:0; max-height: 65vh; overflow:auto }
  ul.cols li { padding: 9px 11px; border-radius: 6px; cursor:pointer; display:flex; justify-content: space-between;
               align-items:center; border:1px solid transparent }
  ul.cols li:hover { background:#0b1220; border-color:#1e293b }
  ul.cols li.active { background:#1e293b; border-color:#3b82f6 }
  ul.cols .name { font-family: ui-monospace, Menlo, Consolas, monospace; font-size:12.5px; word-break: break-all }
  ul.cols .count { font-size: 11px; background:#0b1220; color:#fbbf24; padding: 2px 7px; border-radius: 999px; margin-left:8px; white-space: nowrap}
  .rec-list { display:flex; flex-direction:column; gap: 10px }
  .rec { padding: 12px 14px; border:1px solid #1f2937; border-radius: 10px; background:#0b1220 }
  .rec header { position:static; all:unset; display:flex; justify-content:space-between; align-items:center;
                border:0; background:transparent; padding: 0 0 8px 0 }
  .rec .id { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11.5px; color:#93c5fd }
  .rec .tick { font-size: 11px; background:#1e293b; color:#fcd34d; padding:2px 8px; border-radius: 999px; margin-left: 8px }
  .rec .doc { white-space: pre-wrap; word-break: break-word; font-size: 13.5px; padding: 8px 0;
              border-top: 1px dashed #1f2937; border-bottom: 1px dashed #1f2937 }
  .rec .meta { margin-top:6px; font-size: 12px; display:flex; flex-wrap: wrap; gap: 6px }
  .chip { padding: 2px 7px; background:#111827; border:1px solid #374151; color:#cbd5e1;
          border-radius: 6px; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px }
  .chip.green { color:#86efac; border-color:#065f46; background:#052e25 }
  .chip.red { color:#fca5a5; border-color:#7f1d1d; background:#3f1212 }
  .chip.blue { color:#93c5fd; border-color:#1e3a8a; background:#172554 }
  .chip.purple { color:#d8b4fe; border-color:#581c87; background:#3b0764 }
  .rec .emb { margin-top: 10px; font-family: ui-monospace, Menlo, Consolas, monospace;
              font-size: 11.5px; color:#a5b4fc; background:#0b1220; padding: 7px 10px; border-radius: 6px; border:1px solid #1e293b }
  .toolbar { display:flex; align-items:center; gap:10px; margin-bottom: 12px; flex-wrap:wrap }
  .toolbar label { font-size:12px; opacity:.8 }
  .toolbar input, .toolbar select { background:#0b1220; color:#e2e8f0; border:1px solid #374151;
                                     border-radius:6px; padding: 5px 8px; font-size: 13px }
  button.primary { background:#2563eb; color:#fff; border:0; padding: 7px 13px;
                   border-radius: 7px; font-weight: 500; cursor:pointer; font-size: 13px }
  button.primary:hover { background:#1d4ed8 }
  .empty { opacity:.5; font-size: 13px; padding: 40px 0; text-align:center }
  h2.title { margin:0 0 10px 0; font-size:15px }
  code { font-family: ui-monospace, Menlo, Consolas, monospace }
</style>
</head>
<body>
<header>
  <h1>🧭 AIGameWorld · ChromaDB 数据可视化</h1>
  <div class="sub">PersistentClient 路径：<code id="persist">…</code> · 直接直连 ChromaDB Server API，不走 Node 代理</div>
</header>
<main>
  <aside class="card">
    <div class="stats">
      <div class="stat"><b id="s-col">…</b><span>Collections</span></div>
      <div class="stat"><b id="s-rec">…</b><span>Total Records</span></div>
      <div class="stat"><b id="s-dim">…</b><span>Embedding Dim</span></div>
    </div>
    <h2 class="title">Collections</h2>
    <ul id="col-list" class="cols"></ul>
  </aside>
  <section>
    <div class="card" style="margin-bottom: 14px">
      <h2 class="title" id="curr-title">请选择左侧的一个 collection</h2>
      <div class="toolbar">
        <label>Limit:
          <input id="limit" type="number" value="10" min="1" max="100" style="width: 70px" />
        </label>
        <label>
          <input id="with-emb" type="checkbox" /> 显示 embedding（维度+首尾值预览）
        </label>
        <button class="primary" id="btn-reload">刷新 / Reload</button>
        <span style="margin-left:auto; font-size:12px; opacity:.7" id="status">&nbsp;</span>
      </div>
    </div>
    <div class="card">
      <div id="records" class="rec-list"><div class="empty">左侧选择一个 collection 开始浏览数据</div></div>
    </div>
  </section>
</main>
<script>
const $ = (id) => document.getElementById(id);
const api = (path) => fetch('/_' + path).then(r => { if(!r.ok) return r.text().then(t=>{throw new Error(r.status+': '+t)}); return r.json() });

let cols = [], curr = null;

async function loadList(){
  const data = await api('/collections');
  cols = data.collections;
  const ul = $('col-list');
  ul.innerHTML = '';
  cols.forEach(c => {
    const li = document.createElement('li');
    li.innerHTML = `<span class="name" title="${c.name}">${c.name}</span><span class="count">${c.count}</span>`;
    li.title = `id=${c.id}`;
    li.onclick = () => select(c);
    ul.appendChild(li);
  });
  const total = cols.reduce((s,c)=>s + Math.max(0,c.count),0);
  $('s-col').textContent = cols.length;
  $('s-rec').textContent = total;
  $('s-dim').textContent = '384'; // default all-MiniLM-L6-v2 / BGE-M3 fallback dim
  try {
    const h = await fetch('/').then(r=>r.json());
    $('persist').textContent = h.persist_directory || 'n/a';
  } catch(e) {}
}

function chip(k, v){
  const colors = {type:'purple', period:'blue', memory_type:'purple',
                  tick:'green', importance:'red', world_id:'green', entity_type:'blue',
                  pack:'green', source:'blue', name:'purple', index:'red', key:'purple', scene_id:'blue'};
  return `<span class="chip ${colors[k]||''}" title="${k}: ${v}">${k}=${v}</span>`;
}

function renderRecord(r, i){
  const meta = r.metadatas && r.metadatas[i] ? r.metadatas[i] : {};
  const embPrev = r.embeddings_preview && r.embeddings_preview[i];
  const parts = [];
  Object.entries(meta).forEach(([k,v])=> parts.push(chip(k, JSON.stringify(v))));
  const tick = meta.tick;
  return `<div class="rec">
    <header>
      <span class="id">${r.ids[i]}</span>
      ${tick!=null?`<span class="tick">Tick ${tick}</span>`:''}
    </header>
    <div class="doc">${r.documents[i]}</div>
    ${parts.length?`<div class="meta">${parts.join('')}</div>`:''}
    ${embPrev?`<div class="emb">📦 embedding dim=${embPrev.dim} · 开头数值 [${embPrev.first_values.join(', ')}] · 末尾数值 [${embPrev.last_values.join(', ')}]</div>`:''}
  </div>`;
}

async function loadRecords(){
  if(!curr){ return; }
  $('status').textContent = 'Loading…';
  const lim = parseInt($('limit').value||'10',10);
  const emb = $('with-emb').checked ? 'true' : 'false';
  try {
    const r = await api(`/collections/${encodeURIComponent(curr.name)}/records?limit=${lim}&embeddings=${emb}`);
    const total = curr.count;
    $('curr-title').textContent = `${curr.name}  (count=${total}, 展示前 ${Math.min(lim, r.ids?r.ids.length:0)})`;
    if(!r.ids || !r.ids.length){ $('records').innerHTML='<div class="empty">该 collection 暂无记录</div>'; return; }
    $('records').innerHTML = r.ids.map((_,i)=>renderRecord(r,i)).join('');
    $('status').textContent = `OK · ${r.ids.length} records`;
  } catch(e){
    $('records').innerHTML = `<div class="empty" style="color:#fca5a5">加载失败：${e.message}</div>`;
    $('status').textContent = 'Error';
  }
}

function select(c){
  curr = c;
  document.querySelectorAll('#col-list li').forEach((li,i)=>li.classList.toggle('active', cols[i]===c));
  loadRecords();
}

$('btn-reload').onclick = () => { if(curr) loadRecords(); else loadList() };
$('limit').onchange = () => curr && loadRecords();
$('with-emb').onchange = () => curr && loadRecords();

loadList();
</script>
</body>
</html>"""


@app.get("/viewer", tags=["viewer"], response_class=HTMLResponse)
def viewer_root():
    return HTMLResponse(_VIEWER_HTML)


# ⚠️ 「挂载 ChromaDB 原生 app」移动到 _init_chroma_app() 函数里动态执行
#    因为 chroma_app 要等 argparse --persist-path 解析后才初始化。
#    外层 @app 注册的路由（/, /health, /api/v2/healthcheck, /viewer, /_/*）优先匹配，
#    其他请求（/api/v1/* 和大部分 /api/v2/*）交给 ChromaDB FastAPI app 处理。


def _open_browser_when_ready(url: str, timeout_s: int = 40) -> None:
    """后台线程：轮询服务根路径 /health，成功后用默认浏览器打开 viewer / 指定 URL."""
    import time
    import webbrowser
    from urllib.parse import urlparse

    parsed = urlparse(url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    health_url = root + "/health"

    def _worker():
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                r = _requests.get(health_url, timeout=2)
                if r.status_code == 200:
                    try:
                        webbrowser.open(url, new=2, autoraise=True)
                    except Exception:
                        pass
                    print(f"[viewer] opened in browser → {url}")
                    return
            except (_requests.exceptions.ConnectionError, TimeoutError):
                pass
            except Exception:
                pass
            time.sleep(1.0)
        print(
            f"[viewer] timeout waiting for server up, not opening browser (last tried {health_url})"
        )

    threading.Thread(target=_worker, name="open-browser", daemon=True).start()


def _build_arg_parser():
    import argparse

    p = argparse.ArgumentParser(
        prog="chroma_server",
        description=(
            "ChromaDB HTTP Server (PersistentClient) + health check endpoints "
            "+ embedded /viewer UI for browsing vectors/metadata.\n\n"
            "支持 --persist-path 指定任意 chroma 持久化目录，可与主项目记忆库分端口启动：\n"
            "  · Studio 知识库（默认）: (不传 --persist-path) 本项目 data/chroma，默认端口 8001\n"
            "  · 主项目记忆库: --persist-path E:/Projects/AIGameWorld/backend/data/chroma --port 8002"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--host", default="0.0.0.0", help="绑定地址 (默认: 0.0.0.0)")
    p.add_argument("--port", type=int, default=8001, help="监听端口 (默认: 8001)")
    p.add_argument(
        "--persist-path",
        default=None,
        help="ChromaDB 持久化目录（绝对/相对路径均可；相对路径按当前 shell cwd 解析；不传默认 Studio/data/chroma）",
    )
    p.add_argument(
        "--log-level",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        default="info",
    )
    p.add_argument(
        "--open-browser",
        action="store_true",
        help="server 起来后自动用默认浏览器打开 /viewer",
    )
    p.add_argument(
        "--no-viewer",
        action="store_true",
        help="不注册 /viewer 和 /_/* 路由，只启动纯 ChromaDB + health endpoints",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_arg_parser().parse_args(argv)

    # 1. 先解析 persist_path，注入全局 _CHROMA_DIR
    global _CHROMA_DIR
    _CHROMA_DIR = _resolve_persist_path(args.persist_path)

    # 2. 懒加载 ChromaFastAPI（Settings + PersistentClient + 挂载 chroma_app 到外层 @app）
    _init_chroma_app(_CHROMA_DIR)

    if args.no_viewer:
        global _VIEWER_HTML  # noqa: PLW0603 (we intentionally remove)
        _VIEWER_HTML = ""  # type: ignore[assignment]
        routes_to_remove = [
            r
            for r in app.routes
            if getattr(r, "path", "").startswith("/viewer")
            or getattr(r, "path", "").startswith("/_/")
        ]
        for r in routes_to_remove:
            try:
                app.routes.remove(r)
            except ValueError:
                pass

    # 3. 动态设置 viewer 内部 HTTP 调用的 base_url（解决端口不是 8001 时 _http() 打错端口的问题）
    host = args.host if args.host != "0.0.0.0" else "127.0.0.1"
    base_url = f"http://{host}:{args.port}"
    _set_local_base(base_url)
    viewer_url = base_url + "/viewer"

    print("=" * 72)
    print("  AIGameWorld ChromaDB Server")
    print(f"  · PersistentClient dir: {_CHROMA_DIR}")
    print(f"  · HTTP API:            {base_url}/")
    print(f"  · Health:              {base_url}/health")
    print(f"  · v2 healthcheck:      {base_url}/api/v2/healthcheck")
    if not args.no_viewer:
        print(f"  · Viewer UI:           {viewer_url}")
    if args.open_browser:
        print("  · Auto-open browser:   enabled")
    print("=" * 72)
    print()

    if args.open_browser and not args.no_viewer:
        _open_browser_when_ready(viewer_url)

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
