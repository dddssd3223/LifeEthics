"""검토용 로컬 UI: python -m csatdb serve  → http://localhost:8765

검수 결과는 data/reviews/reviews.jsonl (append-only, git 추적) 과 SQLite reviews 테이블에 동시에 기록된다.
문항 JSON/이미지는 UI 에서 수정하지 않는다.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from .common import DB_PATH, Q_JSON, REVIEWS, ROOT, read_json, now_iso

STATUSES = {"ok": "정상", "needs_review": "검토 필요", "source_error": "출처 오류",
            "crop_error": "crop 오류", "text_error": "텍스트 오류"}

PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>문항 DB 검수</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#f6f7f9;--fg:#1d2330;--mut:#667085;--card:#fff;--line:#e3e6ea;--acc:#2f6fed;--warn:#b54708;--bad:#b42318;--good:#067647}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;background:var(--bg);color:var(--fg)}
header{display:flex;gap:12px;align-items:center;padding:10px 16px;background:var(--card);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:2;flex-wrap:wrap}
header h1{font-size:16px;margin:0 8px 0 0}select,input{font:inherit;padding:4px 6px;border:1px solid var(--line);border-radius:6px}
main{display:grid;grid-template-columns:300px 1fr;height:calc(100vh - 54px)}
#list{overflow:auto;border-right:1px solid var(--line);background:var(--card)}
#list div{padding:6px 12px;border-bottom:1px solid var(--line);cursor:pointer;display:flex;justify-content:space-between;gap:6px}
#list div.sel{background:#e8efff}#list .r{color:var(--warn)}#list .rv{font-size:12px;color:var(--mut)}
#detail{overflow:auto;padding:16px;display:grid;grid-template-columns:minmax(300px,1fr) minmax(300px,1fr);gap:16px;align-content:start}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}
.img img{max-width:100%;border:1px solid var(--line)}
dl{display:grid;grid-template-columns:130px 1fr;margin:0;gap:2px 8px}dt{color:var(--mut)}dd{margin:0}
pre{white-space:pre-wrap;margin:0;font:13px/1.5 inherit}
.btns{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.btns button{font:inherit;padding:6px 10px;border-radius:6px;border:1px solid var(--line);background:#fff;cursor:pointer}
.btns button:hover{border-color:var(--acc)}.btns button.cur{background:var(--acc);color:#fff}
.w{color:var(--warn)}.muted{color:var(--mut)}ol{margin:0;padding-left:20px}
@media (max-width:900px){main{grid-template-columns:1fr}#list{max-height:30vh}#detail{grid-template-columns:1fr}}
</style></head><body>
<header><h1>생윤 원천 문항 DB 검수</h1>
<select id="f"><option value="review">검토 필요만</option><option value="all">전체</option><option value="ok">자동 정상</option><option value="unreviewed">미검수</option></select>
<select id="exam"><option value="">전체 시험</option></select>
<span id="stat" class="muted"></span>
<span class="muted">단축키: ↑/↓ 이동 · 1 정상 · 2 검토 필요 · 3 출처 오류 · 4 crop 오류 · 5 텍스트 오류</span></header>
<main><div id="list"></div><div id="detail"><p class="muted">왼쪽에서 문항을 선택하세요.</p></div></main>
<script>
const ST={ok:"정상",needs_review:"검토 필요",source_error:"출처 오류",crop_error:"crop 오류",text_error:"텍스트 오류"};
let items=[],cur=-1;
const $=s=>document.querySelector(s);
function esc(s){return (s??"").toString().replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]))}
async function load(){
  const r=await fetch(`/api/questions?filter=${$('#f').value}&exam=${encodeURIComponent($('#exam').value)}`);const d=await r.json();
  items=d.items;$('#stat').textContent=`${items.length}건 / 전체 ${d.total} · 검수완료 ${d.reviewed}`;
  if($('#exam').options.length<=1){for(const e of d.exams){const o=document.createElement('option');o.value=e;o.textContent=e;$('#exam').appendChild(o)}}
  $('#list').innerHTML=items.map((q,i)=>`<div data-i="${i}" class="${q.review_required?'r':''}"><span>${esc(q.id)}</span><span class="rv">${q.review?ST[q.review]:''}</span></div>`).join('');
  document.querySelectorAll('#list div').forEach(el=>el.onclick=()=>show(+el.dataset.i));
  if(items.length)show(0);else $('#detail').innerHTML='<p class="muted">해당 문항 없음</p>';
}
async function show(i){
  cur=i;document.querySelectorAll('#list div').forEach(el=>el.classList.toggle('sel',+el.dataset.i===i));
  const el=document.querySelector(`#list div[data-i="${i}"]`);el&&el.scrollIntoView({block:'nearest'});
  const q=await (await fetch('/api/question/'+encodeURIComponent(items[i].id))).json();
  const s=q.source,o=q.original,a=q.answer,x=q.extraction;
  $('#detail').innerHTML=`
  <div class="card img"><img src="/${esc(o.question_image)}" alt="${esc(q.id)}"></div>
  <div>
   <div class="card"><h3 style="margin:0 0 6px">${esc(q.id)}</h3>
    <div class="btns">${Object.entries(ST).map(([k,v],n)=>`<button data-s="${k}" class="${q.review&&q.review.status===k?'cur':''}">${n+1}. ${v}</button>`).join('')}</div>
    <input id="note" placeholder="메모(선택)" style="width:100%" value="${esc(q.review?q.review.note||'':'')}">
    ${q.review?`<p class="muted">최근 검수: ${ST[q.review.status]} · ${esc(q.review.reviewed_at)}</p>`:''}
   </div>
   <div class="card"><dl>
    <dt>출처</dt><dd>${esc(s.original_compilation)}</dd>
    <dt>학년도 / 시행</dt><dd>${esc(s.academic_year)} / ${esc(s.exam_year)}</dd>
    <dt>시험 종류</dt><dd>${esc(s.exam_type)}</dd><dt>문항 번호</dt><dd>${esc(s.question_number)}</dd>
    <dt>원본 페이지</dt><dd>p${esc(s.original_compilation_page)} (${esc(s.split_pdf)} p${esc(s.split_pdf_page)}, ${esc(s.column)}단)</dd>
    <dt>정답</dt><dd>${a.value==null?'<span class="muted">없음 (공식 정답표 미연결)</span>':esc(a.value)+' ('+esc(a.source)+')'}</dd>
    <dt>confidence</dt><dd>${esc(x.confidence)}</dd><dt>review_required</dt><dd>${x.review_required?'<b class="w">true</b>':'false'}</dd>
    <dt>시각 자료</dt><dd>${o.has_visual_material==null?'판정 불가':o.has_visual_material} ${esc((o.visual_material_evidence||[]).join(', '))}</dd>
    <dt>warnings</dt><dd class="w">${[...x.warnings,...(x.qa_flags||[])].map(esc).join('<br>')||'<span class="muted">없음</span>'}</dd>
   </dl></div>
   <div class="card"><b>발문</b><pre>${esc(o.stem)}</pre></div>
   <div class="card"><b>제시문/자료/보기 (OCR)</b><pre>${esc(o.passage)}</pre></div>
   <div class="card"><b>선택지 (OCR)</b><ol>${o.choices.map(c=>`<li>${esc(c.text)}</li>`).join('')}</ol></div>
   <div class="card"><details><summary>raw_text</summary><pre>${esc(o.raw_text)}</pre></details></div>
  </div>`;
  document.querySelectorAll('.btns button').forEach(b=>b.onclick=()=>review(b.dataset.s));
}
async function review(st){
  const id=items[cur].id;
  await fetch('/api/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question_id:id,status:st,note:$('#note').value})});
  items[cur].review=st;document.querySelector(`#list div[data-i="${cur}"] .rv`).textContent=ST[st];
  if(cur+1<items.length)show(cur+1);
}
document.addEventListener('keydown',e=>{if(e.target.tagName==='INPUT')return;
  if(e.key==='ArrowDown'&&cur+1<items.length){e.preventDefault();show(cur+1)}
  if(e.key==='ArrowUp'&&cur>0){e.preventDefault();show(cur-1)}
  const k=Object.keys(ST)[+e.key-1];if(k&&cur>=0)review(k)});
$('#f').onchange=load;$('#exam').onchange=load;load();
</script></body></html>"""


def _latest_reviews() -> dict:
    out = {}
    p = REVIEWS / "reviews.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["question_id"]] = r
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        if path == "/":
            return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if path == "/api/questions":
            qs = urllib.parse.parse_qs(u.query)
            flt = qs.get("filter", ["review"])[0]
            exam = qs.get("exam", [""])[0]
            revs = _latest_reviews()
            items, exams = [], set()
            total = 0
            for jp in sorted(Q_JSON.glob("*.json")):
                r = read_json(jp)
                if not r:
                    continue
                total += 1
                ek = r["source"]["exam_key"]
                exams.add(ek)
                rr = r["extraction"]["review_required"]
                rv = revs.get(r["id"], {}).get("status")
                if exam and ek != exam:
                    continue
                if flt == "review" and not rr:
                    continue
                if flt == "ok" and rr:
                    continue
                if flt == "unreviewed" and rv:
                    continue
                items.append({"id": r["id"], "review_required": rr, "review": rv})
            return self._json({"items": items, "total": total, "reviewed": len(revs), "exams": sorted(exams)})
        if path.startswith("/api/question/"):
            qid = path.rsplit("/", 1)[-1]
            jp = Q_JSON / f"{qid}.json"
            if not jp.exists() or "/" in qid or ".." in qid:
                return self._json({"error": "not found"}, 404)
            r = read_json(jp)
            r["review"] = _latest_reviews().get(qid)
            return self._json(r)
        if path.startswith("/data/questions/images/"):
            p = (ROOT / path.lstrip("/")).resolve()
            if not str(p).startswith(str((ROOT / "data/questions/images").resolve())) or not p.exists():
                return self._send(404, b"not found", "text/plain")
            return self._send(200, p.read_bytes(), "image/png")
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path != "/api/review":
            return self._send(404, b"not found", "text/plain")
        n = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(n) or b"{}")
        qid, status = body.get("question_id"), body.get("status")
        if status not in STATUSES or not qid or not (Q_JSON / f"{qid}.json").exists():
            return self._json({"error": "bad request"}, 400)
        rec = {"question_id": qid, "status": status, "note": body.get("note") or None,
               "reviewer": body.get("reviewer"), "reviewed_at": now_iso()}
        REVIEWS.mkdir(parents=True, exist_ok=True)
        with open(REVIEWS / "reviews.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if DB_PATH.exists():
            con = sqlite3.connect(DB_PATH)
            try:
                con.execute("INSERT INTO reviews (question_id, status, note, reviewer, reviewed_at) VALUES (?,?,?,?,?)",
                            (qid, status, rec["note"], rec["reviewer"], rec["reviewed_at"]))
                con.commit()
            finally:
                con.close()
        return self._json({"ok": True, "review": rec})


def serve(port: int = 8765, host: str = "127.0.0.1"):
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"검수 UI: http://localhost:{port}  (Ctrl+C 로 종료)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
