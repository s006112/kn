HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Quotation</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{--bg:#fff;--ink:#0b0f17;--muted:#6b7280;--line:#e5e7eb;--bad:#b91c1c;--sticky:#0b0f17;--sticky-ink:#fff}
  html,body{margin:0;padding:0;background:var(--bg);color:var(--ink);font:14px/1.4 system-ui,-apple-system,Segoe UI,Roboto,Arial,"Noto Sans","PingFang SC","Microsoft YaHei",sans-serif}
  .container{max-width:1100px;margin:0 auto;padding:16px}
  header{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin-bottom:12px}
  h1{font-size:20px;margin:0}
  .desc{color:var(--muted);font-size:12px}
  .btn{border:1px solid var(--line);background:#f8fafc;color:#111827;border-radius:6px;padding:6px 10px;cursor:pointer}
  table{width:100%;border-collapse:collapse}
  thead th{position:sticky;top:48px;background:#fafafa;border-bottom:1px solid var(--line);padding:8px;text-align:left;font-size:12px;color:#374151}
  tbody td{border-bottom:1px solid var(--line);padding:0}
  tbody tr:hover{background:#fbfbfd}
  td.cell{padding:8px}
  input.cell-input{width:100%;box-sizing:border-box;border:0;padding:8px;background:transparent;font:inherit;color:inherit;outline:none}
  input.cell-input[type="number"]{text-align:right}
  .align-left{text-align:left}.align-right{text-align:right}
  .err{background:#fff1f2}
  .err-msg{color:var(--bad);font-size:11px;padding:4px 8px}
  .totals{margin-top:10px;border:1px solid var(--line);border-radius:8px;overflow:hidden}
  .totals .row{display:flex;justify-content:space-between;padding:8px 12px;border-bottom:1px solid var(--line)}
  .totals .row:last-child{border-bottom:0}
  .strong{font-weight:600}
  .sticky-total{position:sticky;top:0;z-index:10;background:var(--sticky);color:var(--sticky-ink);display:flex;justify-content:center;align-items:center;height:44px;font-size:14px;letter-spacing:.2px}
  .footnote{color:var(--muted);font-size:11px;margin-top:8px}
</style>
</head>
<body>
<div class="sticky-total" id="stickyTotal">Grand Total: —</div>
<div class="container">
  <header>
    <div>
      <h1>Manufacturing Quotation</h1>
      <div class="desc">Inline editable. Live totals. Config-driven schema.</div>
    </div>
    <div class="controls">
      <button class="btn" id="resetBtn" title="Restore defaults">Reset</button>
      <button class="btn" id="exportBtn" title="Export CSV">Export</button>
    </div>
  </header>

  <section id="grid"></section>

  <section class="totals" aria-live="polite">
    <div class="row"><div>Subtotal</div><div id="subtotal" class="strong">—</div></div>
    <div class="row strong"><div>Grand Total</div><div id="grandTotal">—</div></div>
  </section>

  <div class="footnote">Values auto-save to this browser. Reset clears local data and reloads defaults.</div>
</div>

<!-- MOVE JSON BEFORE MAIN SCRIPT -->
<script id="cfg-json" type="application/json">{CFG_JSON}</script>
<script id="data-json" type="application/json">{DATA_JSON}</script>

<script>
/* ---------- Read injected config ---------- */
const CONFIG = JSON.parse(document.getElementById('cfg-json').textContent);
const DEFAULT_DATA = JSON.parse(document.getElementById('data-json').textContent);

/* ---------- Utilities ---------- */
const LS_KEY = 'quote.rows.v1';
const clampNonNeg = (v)=> Math.max(0, isFinite(v)? v : 0);
function parseNum(v){ if(v===null||v===undefined||v==="") return 0; const n = Number(v); return isFinite(n)? n : NaN; }
function fmt(n, digits){ try{ return Number(n).toLocaleString(undefined,{minimumFractionDigits:digits, maximumFractionDigits:digits}); }catch(_){ return String(n); } }
function fmtAuto(n){ const fixed = Number(n).toFixed(4); return Number(fixed).toLocaleString(undefined,{minimumFractionDigits:0, maximumFractionDigits:4}); }
function clone(o){ return JSON.parse(JSON.stringify(o)); }

/* ---------- Persistence ---------- */
function loadRows(){
  const raw = localStorage.getItem(LS_KEY);
  if(!raw){ return clone(DEFAULT_DATA); }
  try{
    const data = JSON.parse(raw);
    return data.map(r => ({
      item: r.item ?? "",
      remarks: r.remarks ?? "",
      unit_cost: clampNonNeg(parseNum(r.unit_cost)),
      qty: clampNonNeg(parseNum(r.qty)),
      line_total: 0
    }));
  }catch(e){
    console.warn('Bad local data, resetting.', e);
    localStorage.removeItem(LS_KEY);
    return clone(DEFAULT_DATA);
  }
}
function saveRows(rows){ localStorage.setItem(LS_KEY, JSON.stringify(rows)); }

/* ---------- Calculations ---------- */
function computeRow(r){
  const uc = clampNonNeg(parseNum(r.unit_cost));
  const q  = clampNonNeg(parseNum(r.qty));
  return { ...r, unit_cost: uc, qty: q, line_total: uc * q };
}
function computeTotals(rows){
  let subtotal = 0;
  rows.forEach(r => { subtotal += clampNonNeg(parseNum(r.line_total)); });
  return { subtotal, grand_total: subtotal };
}

/* ---------- Rendering ---------- */
function render(){
  const root = document.getElementById('grid');
  const cols = CONFIG.columns;
  let rows = loadRows().map(computeRow);

  const table = document.createElement('table');
  const thead = document.createElement('thead');
  const trh = document.createElement('tr');
  cols.forEach(c=>{
    const th = document.createElement('th');
    th.textContent = c.label;
    trh.appendChild(th);
  });
  thead.appendChild(trh);

  const tbody = document.createElement('tbody');

  rows.forEach((row, ridx)=>{
    const tr = document.createElement('tr');
    let rowHasErr = false;

    cols.forEach((c)=>{
      const td = document.createElement('td');
      td.className = 'cell ' + (c.align==='right'?'align-right':'align-left');

      if(!c.editable){
        const span = document.createElement('div');
        span.className = 'cell';
        let val = row[c.key];
        if(c.type === 'number'){
          if(c.key === 'line_total'){ val = fmtAuto(val); } else { val = fmt(val, c.precision||0); }
        }
        span.textContent = val;
        td.appendChild(span);
      }else{
        const input = document.createElement('input');
        if(c.type === 'text'){ input.type = 'text'; }
        else{
          input.type = 'number';
          input.step = c.precision ? (1/Math.pow(10, c.precision)).toFixed(c.precision) : '1';
          input.min = (c.min!==undefined) ? String(c.min) : '0';
        }
        input.classList.add('cell-input');
        if(c.type==='number'){ input.classList.add('align-right'); }
        input.value = (c.type==='number') ? String(row[c.key]) : (row[c.key] ?? '');
        input.dataset.row = String(ridx);
        input.dataset.key = c.key;

        if(c.type==='number'){
          const parsed = parseNum(input.value);
          if(isNaN(parsed) || parsed < (c.min ?? 0)){ rowHasErr = True; td.classList.add('err'); }
        }

        td.appendChild(input);
      }
      tr.appendChild(td);
    });

    if(rowHasErr){
      const trm = document.createElement('tr');
      const tdm = document.createElement('td');
      tdm.colSpan = cols.length;
      tdm.innerHTML = '<div class="err-msg">Invalid number in this row. Values must be ≥ 0.</div>';
      tr.classList.add('err');
      tbody.appendChild(tr);
      tbody.appendChild(trm).appendChild(tdm);
    }else{
      tbody.appendChild(tr);
    }
  });

  table.appendChild(thead);
  table.appendChild(tbody);
  root.innerHTML = '';
  root.appendChild(table);

  bindInputs();
  updateTotals();
}

function bindInputs(){
  document.querySelectorAll('input.cell-input').forEach(inp=>{
    inp.addEventListener('input', onEdit);
    inp.addEventListener('change', onEdit);
    inp.addEventListener('keydown', (e)=>{
      if(e.key==='Enter'){
        e.preventDefault();
        const inputs = Array.from(document.querySelectorAll('input.cell-input'));
        const idx = inputs.indexOf(e.currentTarget);
        const next = e.shiftKey ? inputs[idx-1] : inputs[idx+1];
        if(next){ next.focus(); next.select?.(); }
      }
    });
  });
}

function onEdit(e){
  const target = e.currentTarget;
  const rowIdx = Number(target.dataset.row);
  const key = target.dataset.key;
  const val = target.type==='number' ? parseNum(target.value) : target.value;

  let rows = loadRows();
  if(!rows[rowIdx]) return;

  if(target.type==='number'){
    if(isNaN(val) || val < 0){ target.parentElement.classList.add('err'); }
    else{ target.parentElement.classList.remove('err'); rows[rowIdx][key] = Math.max(0, val); }
  }else{
    rows[rowIdx][key] = String(val);
  }

  rows[rowIdx] = computeRow(rows[rowIdx]);
  saveRows(rows);
  refreshRowDisplay(rowIdx, rows[rowIdx]);
  updateTotals();
}

function refreshRowDisplay(rowIdx, row){
  const table = document.querySelector('table');
  const tbody = table.tBodies[0];
  if(document.querySelector('.err')){ render(); return; }
  const tr = tbody.rows[rowIdx];
  if(!tr){ render(); return; }
  const cols = CONFIG.columns;
  for(let cidx=0;cidx<cols.length;cidx++){
    const c = cols[cidx];
    const td = tr.cells[cidx];
    if(!c.editable && c.key==='line_total'){
      td.firstChild.textContent = fmtAuto(row.line_total);
    }
  }
}

function updateTotals(){
  const rows = loadRows().map(computeRow);
  const {subtotal, grand_total} = computeTotals(rows);
  document.getElementById('subtotal').textContent = fmtAuto(subtotal);
  document.getElementById('grandTotal').textContent = fmtAuto(grand_total);
  document.getElementById('stickyTotal').textContent = 'Grand Total: ' + fmtAuto(grand_total);
}

/* ---------- Actions ---------- */
document.addEventListener('click', (e)=>{
  if(e.target && e.target.id === 'resetBtn'){ localStorage.removeItem(LS_KEY); location.reload(); }
  if(e.target && e.target.id === 'exportBtn'){ exportCSV(); }
});

function csvEscape(s){
  const t = String(s).replaceAll('"','""');
  return /[",\\n]/.test(t) ? `"${t}"` : t;
}

function exportCSV(){
  const cols = CONFIG.columns;
  const rows = loadRows().map(computeRow);
  const {subtotal, grand_total} = computeTotals(rows);

  const lines = [];
  lines.push(cols.map(c=>csvEscape(c.label)).join(','));
  rows.forEach(r=>{
    lines.push(cols.map(c=>{
      let v = r[c.key];
      return csvEscape(v);
    }).join(','));
  });

  // Footer lines aligned to last column
  const pad = Array(cols.length-2).fill('');
  lines.push([csvEscape('Subtotal'), ...pad, csvEscape(subtotal)].join(','));
  lines.push([csvEscape('Grand Total'), ...pad, csvEscape(grand_total)].join(','));

  const blob = new Blob([lines.join('\\n')], {type:'text/csv;charset=utf-8;'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'quotation.csv';
  document.body.appendChild(a); a.click(); URL.revokeObjectURL(url); a.remove();
}

/* ---------- Boot ---------- */
render();
</script>
</body>
</html>
"""