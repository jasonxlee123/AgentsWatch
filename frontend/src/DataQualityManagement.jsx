import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowClockwise,
  CheckCircle,
  CloudArrowUp,
  DownloadSimple,
  FileXls,
  Info,
  SpinnerGap,
  UploadSimple,
  WarningCircle,
  XCircle,
} from "@phosphor-icons/react";
import { resolveAppRequestPath } from "./basePathUtils.js";

const ENTITY_LABELS = ["部门", "人员", "服务器", "运行时", "智能体"];
const STATUS_LABELS = {
  uploaded: "已上传",
  validating: "校验中",
  ready: "待确认",
  committing: "提交中",
  committed: "已入库",
  rejected: "校验未通过",
  failed: "提交失败",
  superseded: "已失效",
};

function formatTime(value) {
  return String(value || "").replace("T", " ").replace(/([+-]\d{2}:\d{2}|Z)$/, "").slice(0, 19) || "-";
}

function entitySummary(batch, label) {
  return batch?.summary?.entities?.[label] || { total: 0, new: 0, changed: 0, unchanged: 0, invalid: 0 };
}

export function DataQualityManagement({ dataSource, requestJson, canManage = false, onImported }) {
  const inputRef = useRef(null);
  const [file, setFile] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [batch, setBatch] = useState(null);
  const [history, setHistory] = useState([]);

  async function loadHistory() {
    if (dataSource !== "api") return;
    try {
      const response = await requestJson("/api/v1/imports?limit=12");
      setHistory(response.data || []);
    } catch (historyError) {
      setError(historyError.message);
    }
  }

  useEffect(() => { loadHistory(); }, [dataSource, requestJson]);

  function chooseFile(selectedFile) {
    setError("");
    setBatch(null);
    if (!selectedFile) { setFile(null); return; }
    if (!selectedFile.name.toLowerCase().endsWith(".xlsx")) {
      setFile(null);
      setError("请选择 .xlsx 格式的 Excel 文件。");
      return;
    }
    if (selectedFile.size > 10 * 1024 * 1024) {
      setFile(null);
      setError("文件不能超过 10 MB。");
      return;
    }
    setFile(selectedFile);
  }

  async function preview() {
    if (!file || !canManage) return;
    setBusy("preview"); setError("");
    const body = new FormData();
    body.append("file", file);
    try {
      const response = await requestJson("/api/v1/imports/preview", { method: "POST", body });
      setBatch(response.data);
      await loadHistory();
    } catch (previewError) {
      setError(previewError.message);
    } finally {
      setBusy("");
    }
  }

  async function commit() {
    if (!batch || batch.status !== "ready" || !canManage) return;
    if (!window.confirm(`确认将 ${batch.summary?.rows || 0} 行数据一次性写入数据库吗？`)) return;
    setBusy("commit"); setError("");
    try {
      const response = await requestJson(`/api/v1/imports/${batch.import_batch_id}/commit`, { method: "POST", body: "{}" });
      setBatch(response.data);
      await loadHistory();
      onImported?.();
    } catch (commitError) {
      setError(commitError.message);
      try {
        const response = await requestJson(`/api/v1/imports/${batch.import_batch_id}`);
        setBatch(response.data);
      } catch { /* retain the preview already shown */ }
    } finally {
      setBusy("");
    }
  }

  async function openBatch(batchId) {
    if (dataSource !== "api") return;
    setBusy("load"); setError("");
    try {
      const response = await requestJson(`/api/v1/imports/${batchId}`);
      setBatch(response.data);
      setFile(null);
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setBusy("");
    }
  }

  const rows = batch?.rows || [];
  const issues = batch?.issues || [];
  const rowById = useMemo(() => new Map(rows.map((row) => [row.import_row_id, row])), [rows]);
  const ready = batch?.status === "ready";

  return (
    <section className="asset-page import-page" aria-labelledby="import-page-title">
      <div className="asset-page-heading import-page-heading">
        <div><span className="eyebrow">一个文件，整批入库</span><h2 id="import-page-title">数据批量导入</h2><p>导入服务器、运行时、智能体、部门和人员。系统先校验预览，确认后一次性写入。</p></div>
        <a className="secondary-action import-template-action" href={resolveAppRequestPath("/api/v1/imports/template")}><DownloadSimple size={16} />下载 Excel 模板</a>
      </div>

      {!canManage && <div className="permission-banner"><Info size={17} />当前账号可查看导入记录，但没有上传和提交权限。</div>}
      {error && <div className="management-error import-error"><XCircle size={17} />{error}</div>}

      <div className="import-workflow">
        <article className="import-upload-card">
          <div className="import-step-title"><span>1</span><div><strong>选择 Excel 文件</strong><small>仅支持标准模板，最大 10 MB、2000 行</small></div></div>
          <button
            type="button"
            className={`import-dropzone ${dragging ? "import-dropzone--dragging" : ""}`}
            disabled={!canManage || busy === "preview"}
            onClick={() => inputRef.current?.click()}
            onDragOver={(event) => { event.preventDefault(); if (canManage) setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => { event.preventDefault(); setDragging(false); chooseFile(event.dataTransfer.files?.[0]); }}
          >
            <input ref={inputRef} type="file" accept=".xlsx" hidden onChange={(event) => chooseFile(event.target.files?.[0])} />
            {file ? <><FileXls size={31} /><strong>{file.name}</strong><span>{(file.size / 1024).toFixed(1)} KB · 点击可重新选择</span></> : <><CloudArrowUp size={34} /><strong>拖入文件或点击选择</strong><span>请保留五个工作表及其表头</span></>}
          </button>
          <button type="button" className="primary-action import-preview-action" disabled={!file || !canManage || Boolean(busy)} onClick={preview}>
            {busy === "preview" ? <SpinnerGap className="spin" size={17} /> : <UploadSimple size={17} />}上传并校验
          </button>
        </article>

        <article className="import-preview-card">
          <div className="import-step-title"><span>2</span><div><strong>核对导入结果</strong><small>编码相同更新，编码不同新增</small></div></div>
          {!batch ? <div className="import-preview-empty"><FileXls size={30} /><strong>等待上传文件</strong><span>校验后会在这里显示五类数据的新增、更新与错误数量。</span></div> : <>
            <div className={`import-batch-state import-batch-state--${batch.status}`}>
              {ready || batch.status === "committed" ? <CheckCircle size={20} /> : <WarningCircle size={20} />}
              <span><strong>{STATUS_LABELS[batch.status] || batch.status}</strong><small>{batch.file_name} · {batch.summary?.rows || rows.length} 行</small></span>
            </div>
            <div className="import-entity-grid">
              {ENTITY_LABELS.map((label) => { const summary = entitySummary(batch, label); return <div key={label}><strong>{label}</strong><span>{summary.total} 行</span><small><i>新增 {summary.new}</i><i>更新 {summary.changed}</i>{summary.invalid > 0 && <i className="import-invalid">错误 {summary.invalid}</i>}</small></div>; })}
            </div>
            <div className="import-result-note"><Info size={15} /><span>{issues.length ? `发现 ${issues.length} 个错误，请修正 Excel 后重新上传。` : `校验通过：新增 ${rows.filter((row) => row.status === "new").length} 行，更新 ${rows.filter((row) => row.status === "changed").length} 行，不变 ${rows.filter((row) => row.status === "unchanged").length} 行。`}</span></div>
          </>}
        </article>

        <article className="import-commit-card">
          <div className="import-step-title"><span>3</span><div><strong>整批写入数据库</strong><small>提交过程不可拆分，失败不会留下部分数据</small></div></div>
          <div className="import-commit-body"><CheckCircle size={30} /><strong>{batch?.status === "committed" ? "本批次已完成入库" : ready ? "已具备提交条件" : "等待校验通过"}</strong><span>{batch?.status === "committed" ? `完成时间 ${formatTime(batch.committed_at)}` : "所有对象会按部门、人员、服务器、运行时、智能体的顺序写入。"}</span></div>
          <button type="button" className="primary-action import-commit-action" disabled={!ready || !canManage || Boolean(busy)} onClick={commit}>
            {busy === "commit" ? <SpinnerGap className="spin" size={17} /> : <CheckCircle size={17} />}确认并整批入库
          </button>
        </article>
      </div>

      {batch && <section className="import-detail-section">
        <div className="import-section-heading"><div><strong>{issues.length ? "校验错误" : "行级预览"}</strong><span>{issues.length ? "根据工作表和行号修正后重新上传" : "本批次没有校验错误"}</span></div><button type="button" onClick={() => openBatch(batch.import_batch_id)} disabled={Boolean(busy)}><ArrowClockwise size={15} />刷新</button></div>
        {issues.length ? <div className="import-issue-table"><div className="import-issue-head"><span>工作表 / 行</span><span>字段</span><span>错误说明</span></div>{issues.map((issue) => { const row = rowById.get(issue.import_row_id); return <div className="import-issue-row" key={issue.import_issue_id}><span>{row?.sheet_name || "批次"} / {row?.row_number || "-"}</span><code>{issue.field_path || "-"}</code><span>{issue.message}</span></div>; })}</div> : <div className="import-row-table"><div className="import-row-head"><span>工作表 / 行</span><span>对象编码</span><span>对象名称</span><span>结果</span></div>{rows.slice(0, 100).map((row) => { const payload = row.normalized_payload || {}; const code = payload.department_code || payload.employee_no || payload.server_code || payload.runtime_code || payload.agent_code; const name = payload.name || payload.display_name; return <div className="import-row" key={row.import_row_id}><span>{row.sheet_name} / {row.row_number}</span><code>{code}</code><span>{name || "-"}</span><span className={`import-row-status import-row-status--${row.status}`}>{row.status === "new" ? "新增" : row.status === "changed" ? "更新" : row.status === "committed" ? "已入库" : "不变"}</span></div>; })}</div>}
      </section>}

      <section className="import-history-section">
        <div className="import-section-heading"><div><strong>最近导入</strong><span>查看最近 12 个上传批次</span></div><button type="button" onClick={loadHistory}><ArrowClockwise size={15} />刷新</button></div>
        <div className="import-history-list">{history.map((item) => <button type="button" key={item.import_batch_id} onClick={() => openBatch(item.import_batch_id)}><FileXls size={18} /><span><strong>{item.file_name}</strong><small>{formatTime(item.created_at)} · {item.actor_ref}</small></span><em className={`import-history-status import-history-status--${item.status}`}>{STATUS_LABELS[item.status] || item.status}</em><i>{item.rows} 行</i></button>)}{!history.length && <div className="import-history-empty">暂无导入记录</div>}</div>
      </section>
    </section>
  );
}
