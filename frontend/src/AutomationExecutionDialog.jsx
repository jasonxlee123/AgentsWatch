import { useEffect, useState } from "react";
import {
  ArrowsClockwise,
  CaretRight,
  Clock,
  ListChecks,
  Play,
  WarningCircle,
} from "@phosphor-icons/react";
import { groupOperationsByCategory, prepareOperationParameters, RISK_LABELS } from "./serverOperationUtils.js";

const operationIsAvailable = () => "";

export function AutomationExecutionDialog({
  operations,
  operationUnavailableReason = operationIsAvailable,
  loading,
  targets,
  targetId,
  targetName,
  targetUnit,
  emptyMessage,
  submitting,
  error,
  onCancel,
  onSubmit,
}) {
  const [selectedCode, setSelectedCode] = useState(() => operations.find((item) => !operationUnavailableReason(item))?.code || "");
  const [rawParameters, setRawParameters] = useState({});
  const [confirmed, setConfirmed] = useState(false);
  const [localError, setLocalError] = useState("");

  useEffect(() => {
    const selectedOperation = operations.find((item) => item.code === selectedCode);
    if (!selectedOperation || operationUnavailableReason(selectedOperation)) {
      setSelectedCode(operations.find((item) => !operationUnavailableReason(item))?.code || "");
    }
  }, [operationUnavailableReason, operations, selectedCode]);

  const selected = operations.find((item) => item.code === selectedCode);
  const grouped = groupOperationsByCategory(operations);
  const requiresConfirmation = selected && selected.risk_level !== "low";
  const exceedsBatchLimit = selected && !selected.supports_batch && targets.length > 1;
  const submit = (event) => {
    event.preventDefault();
    if (!selected) return;
    try {
      setLocalError("");
      onSubmit(selected, prepareOperationParameters(selected, rawParameters));
    } catch (validationError) {
      setLocalError(validationError.message);
    }
  };

  return <form className="task-execution-form" onSubmit={submit}>
    {loading ? <div className="task-dialog-loading"><ArrowsClockwise size={24} className="spin" />正在读取自动化任务库</div> : <div className="task-execution-layout">
      <aside className="task-catalog" aria-label="可执行任务">
        <p className="task-catalog-hint">已展示当前资源类型的全部已发布任务</p>
        {Object.entries(grouped).map(([category, items]) => <div key={category}><h4>{category}</h4>{items.map((operation) => { const unavailableReason = operationUnavailableReason(operation); return <button type="button" key={operation.code} disabled={Boolean(unavailableReason)} title={unavailableReason || undefined} className={unavailableReason ? "task-choice task-choice--unavailable" : operation.code === selectedCode ? "task-choice task-choice--selected" : "task-choice"} onClick={() => { setSelectedCode(operation.code); setRawParameters({}); setConfirmed(false); setLocalError(""); }}><span><strong>{operation.name}</strong><small>v{operation.version}</small>{unavailableReason && <em>{unavailableReason}</em>}</span><CaretRight size={14} /></button>; })}</div>)}
        {!operations.length && <span className="task-catalog-empty">{emptyMessage}</span>}
      </aside>
      {selected && <section className="task-review">
        <div className="task-review-heading"><span className="task-review-icon"><ListChecks size={20} /></span><div><h4>{selected.name}</h4><p>{selected.description}</p></div></div>
        <div className="task-metadata"><span className={`task-risk task-risk--${selected.risk_level}`}><WarningCircle size={14} />{RISK_LABELS[selected.risk_level] || selected.risk_level}</span><span><Clock size={14} />超时 {selected.timeout_seconds} 秒</span><span>{selected.requires_become ? "需要 sudo" : "无需 sudo"}</span><span>{selected.supports_batch ? "支持批量" : `仅限单${targetUnit}`}</span></div>
        <div className="task-targets"><div><strong>执行目标</strong><span>{targets.length} {targetUnit}</span></div><div>{targets.slice(0, 8).map((target) => <span key={targetId(target)}>{targetName(target)}</span>)}{targets.length > 8 && <span>另 {targets.length - 8} {targetUnit}</span>}</div></div>
        {exceedsBatchLimit && <div className="management-form-error task-inline-error">该任务仅支持单个目标，请返回列表减少勾选数量后重试。</div>}
        {Object.keys(selected.parameters || {}).length > 0 && <div className="task-parameters"><h4>任务参数</h4>{Object.entries(selected.parameters).map(([name, definition]) => <label key={name}><span>{definition.label || name}{definition.required && <i>*</i>}</span>{definition.type === "array" ? <textarea rows="4" value={rawParameters[name] || ""} placeholder={definition.placeholder || "每行一个值"} onChange={(event) => setRawParameters((current) => ({ ...current, [name]: event.target.value }))} /> : definition.type === "boolean" ? <input type="checkbox" checked={Boolean(rawParameters[name])} onChange={(event) => setRawParameters((current) => ({ ...current, [name]: event.target.checked }))} /> : definition.enum ? <select value={rawParameters[name] || ""} onChange={(event) => setRawParameters((current) => ({ ...current, [name]: event.target.value }))}><option value="">请选择</option>{definition.enum.map((value) => <option key={value} value={value}>{value}</option>)}</select> : <input type={definition.type === "integer" || definition.type === "number" ? "number" : "text"} value={rawParameters[name] || ""} placeholder={definition.placeholder || ""} onChange={(event) => setRawParameters((current) => ({ ...current, [name]: event.target.value }))} />}<small>{definition.description}</small></label>)}</div>}
        {requiresConfirmation && <label className="task-risk-confirm"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />我已核对任务、参数和 {targets.length} {targetUnit}执行目标</label>}
      </section>}
    </div>}
    {(error || localError) && <div className="management-form-error">{error || localError}</div>}
    <footer className="management-dialog-actions"><button type="button" disabled={submitting} onClick={onCancel}>取消</button><button className="primary-action" disabled={loading || submitting || !selected || !targets.length || exceedsBatchLimit || (requiresConfirmation && !confirmed)}><Play size={16} />{submitting ? "正在下发" : "确认下发"}</button></footer>
  </form>;
}
