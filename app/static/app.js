"use strict";

const state = {
  file: null,
  system: null,
  jobId: null,
  polling: false,
  lastSegmentCount: 0,
  lastJobStatus: null,
};

const $ = (id) => document.getElementById(id);
const modelSelect = $("model");
const languageSelect = $("language");
const taskSelect = $("task");
const beamSelect = $("beam");
const vadInput = $("vad");
const txtTimestamps = $("txtTimestamps");
const promptInput = $("prompt");
const formatsBox = $("formats");
const fileInput = $("fileInput");
const dropzone = $("dropzone");
const startBtn = $("startBtn");
const cancelBtn = $("cancelBtn");
const uploadWrap = $("uploadWrap");
const jobWrap = $("jobWrap");
const uploadBar = $("uploadBar");
const jobBar = $("jobBar");
const uploadPct = $("uploadPct");
const jobPct = $("jobPct");
const uploadLabel = $("uploadLabel");
const jobLabel = $("jobLabel");
const notice = $("notice");
const noticeText = $("noticeText");
const statusText = $("statusText");
const statusSub = $("statusSub");
const busy = $("busy");
const metaList = $("metaList");
const resultWrap = $("resultWrap");
const segmentBody = $("segmentBody");
const fullText = $("fullText");
const downloads = $("downloads");

function formatBytes(value) {
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${value} B`;
}

function formatClock(seconds) {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
           : `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const NOTICE_KINDS = {
  information: "info",
  success: "success",
  warning: "warning",
  error: "error",
};

function setNotice(text, kind = "information") {
  notice.className = `ts-notice ts-notice--${NOTICE_KINDS[kind] || "info"}`;
  noticeText.textContent = text;
}

function setBusy(active) {
  busy.hidden = !active;
}

function setStatus(text, sub = "") {
  statusText.textContent = text;
  statusSub.textContent = sub;
}

function setBar(bar, pct, labelEl, pctEl) {
  const clamped = Math.max(0, Math.min(100, pct || 0));
  bar.style.transform = `scaleX(${clamped / 100})`;
  const parent = bar.closest("[role='progressbar']");
  if (parent) parent.setAttribute("aria-valuenow", String(Math.round(clamped)));
  if (labelEl) labelEl.textContent = `${clamped.toFixed(0)}%`;
  if (pctEl) pctEl.textContent = `${clamped.toFixed(0)}%`;
}

function fillSelect(select, items, selected) {
  select.innerHTML = "";
  for (const item of items) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.label;
    if (item.id === selected) option.selected = true;
    select.appendChild(option);
  }
}

function buildFormats(formats) {
  formatsBox.innerHTML = "";
  for (const fmt of formats) {
    const label = document.createElement("label");
    label.className = "ts-check";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.id = `fmt-${fmt}`;
    input.value = fmt;
    input.checked = fmt === "srt" || fmt === "txt";
    const text = document.createElement("span");
    text.className = "ts-check__text";
    text.textContent = fmt.toUpperCase();
    label.append(input, text);
    formatsBox.appendChild(label);
  }
}

async function loadSystem() {
  try {
    const response = await fetch("/api/system", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.system = await response.json();
    if (!modelSelect.options.length) {
      fillSelect(modelSelect, state.system.models, state.system.default_model);
      fillSelect(languageSelect, state.system.languages, "ja");
      fillSelect(taskSelect, state.system.tasks, "transcribe");
      buildFormats(state.system.formats);
    }
  } catch (error) {
    setNotice("設定を読み込めませんでした。ページを再読み込みしてください。", "error");
  }
}

function selectFile(file) {
  if (!file) return;
  state.file = file;
  $("fileInfo").textContent = `${file.name}（${formatBytes(file.size)}）`;
  startBtn.disabled = false;
  setNotice("準備ができました。「文字起こしを開始」を押してください。", "information");
  setStatus("待機中", "");
}

fileInput.addEventListener("change", () => selectFile(fileInput.files[0]));

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    fileInput.click();
  }
});

for (const eventName of ["dragenter", "dragover"]) {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.add("is-dragover");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.remove("is-dragover");
  });
}
dropzone.addEventListener("drop", (event) => {
  const file = event.dataTransfer?.files?.[0];
  if (file) selectFile(file);
});

function getSettings() {
  const formats = [...formatsBox.querySelectorAll("input:checked")].map((input) => input.value);
  return {
    model: modelSelect.value,
    language: languageSelect.value,
    task: taskSelect.value,
    vad: vadInput.checked,
    beam_size: Number(beamSelect.value),
    initial_prompt: promptInput.value.trim(),
    formats,
    txt_timestamps: txtTimestamps.checked,
  };
}

async function apiJson(url, options) {
  const response = await fetch(url, options);
  let data = {};
  try {
    data = await response.json();
  } catch (error) {
    data = {};
  }
  if (!response.ok) {
    throw new Error(data.detail || `HTTP ${response.status}`);
  }
  return data;
}

function putChunk(url, blob, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        let detail = `HTTP ${xhr.status}`;
        try {
          detail = JSON.parse(xhr.responseText).detail || detail;
        } catch (error) {
          /* ignore */
        }
        reject(new Error(detail));
      }
    };
    xhr.onerror = () => reject(new Error("ネットワークエラー"));
    xhr.onabort = () => reject(new Error("アップロードを中止しました"));
    xhr.send(blob);
  });
}

async function uploadFile(file, init) {
  const total = init.total_chunks;
  const chunkSize = init.chunk_size;
  const concurrency = Math.max(1, Math.min(init.concurrency || 4, total));
  const loaded = new Array(total).fill(0);
  let nextIndex = 0;

  const update = () => {
    const done = loaded.reduce((sum, value) => sum + value, 0);
    const pct = file.size ? (done / file.size) * 100 : 0;
    setBar(uploadBar, pct, uploadPct, null);
    uploadLabel.textContent = `アップロード中… ${formatBytes(done)} / ${formatBytes(file.size)}`;
  };
  update();

  async function uploadOne(index) {
    const start = index * chunkSize;
    const end = Math.min(file.size, start + chunkSize);
    const blob = file.slice(start, end);
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        await putChunk(`/api/uploads/${init.upload_id}/chunks/${index}`, blob, (sent) => {
          loaded[index] = Math.min(sent, blob.size);
          update();
        });
        loaded[index] = blob.size;
        update();
        return;
      } catch (error) {
        loaded[index] = 0;
        update();
        if (attempt === 3) throw error;
        await sleep(700 * attempt);
      }
    }
  }

  async function worker() {
    while (true) {
      const index = nextIndex;
      nextIndex += 1;
      if (index >= total) return;
      await uploadOne(index);
    }
  }

  await Promise.all(Array.from({ length: concurrency }, () => worker()));
}

function resetResult() {
  state.lastSegmentCount = 0;
  state.lastJobStatus = null;
  resultWrap.hidden = true;
  metaList.hidden = true;
  metaList.innerHTML = "";
  segmentBody.innerHTML = "";
  downloads.innerHTML = "";
  fullText.value = "";
  setBar(jobBar, 0, jobPct, null);
}

async function start() {
  if (!state.file || !state.system) return;
  const settings = getSettings();
  if (!settings.formats.length) {
    setNotice("出力形式を1つ以上選択してください。", "error");
    return;
  }

  startBtn.disabled = true;
  cancelBtn.hidden = true;
  resetResult();
  uploadWrap.hidden = false;
  jobWrap.hidden = true;
  setBusy(true);
  setNotice("アップロードを開始しています…", "information");
  setStatus("アップロード中", "");

  try {
    const init = await apiJson("/api/uploads/init", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: state.file.name,
        size: state.file.size,
        content_type: state.file.type || "",
      }),
    });

    await uploadFile(state.file, init);
    setBar(uploadBar, 100, uploadPct, null);
    uploadLabel.textContent = "アップロード完了";
    setNotice("アップロードが完了しました。処理を開始します…", "success");

    const response = await apiJson(`/api/uploads/${init.upload_id}/complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    });

    state.jobId = response.job_id;
    uploadWrap.hidden = true;
    jobWrap.hidden = false;
    cancelBtn.hidden = false;
    setStatus("処理待ち", "");
    pollJob();
  } catch (error) {
    uploadWrap.hidden = true;
    jobWrap.hidden = true;
    setBusy(false);
    startBtn.disabled = false;
    setNotice(`開始できませんでした: ${error.message}`, "error");
    setStatus("エラー", error.message);
  }
}

async function pollJob() {
  if (!state.jobId || state.polling) return;
  state.polling = true;
  try {
    const response = await fetch(`/api/jobs/${state.jobId}`, { cache: "no-store" });
    if (response.status === 404) {
      throw new Error("ジョブが見つかりません（サーバーが再起動された可能性があります）");
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const job = await response.json();
    renderJob(job);
    const finished = ["done", "error", "cancelled"].includes(job.status);
    if (finished) {
      state.polling = false;
      setBusy(false);
      cancelBtn.hidden = true;
      startBtn.disabled = false;
      return;
    }
  } catch (error) {
    state.polling = false;
    setBusy(false);
    cancelBtn.hidden = true;
    startBtn.disabled = false;
    setNotice(`状態の取得に失敗しました: ${error.message}`, "error");
    return;
  }
  state.polling = false;
  setTimeout(pollJob, 1200);
}

function renderJob(job) {
  const pct = job.status === "done" ? 100 : job.progress || 0;
  setBar(jobBar, pct, jobPct, null);

  if (job.status === "queued") {
    jobLabel.textContent = "処理待ち…";
    setStatus("処理待ち", job.message || "");
  } else if (job.status === "loading" || job.status === "processing") {
    jobLabel.textContent = job.message || "処理中…";
    setStatus(job.message || "処理中", `${job.segment_count || 0} セグメント`);
    setNotice("文字起こしを実行しています。完了までそのままお待ちください。", "information");
  } else if (job.status === "done") {
    jobLabel.textContent = "完了";
    setStatus("完了", `${job.segment_count || 0} セグメント`);
    setNotice("文字起こしが完了しました。結果をダウンロードできます。", "success");
  } else if (job.status === "cancelled") {
    jobLabel.textContent = "キャンセル";
    setStatus("キャンセル", job.message || "");
    setNotice("処理をキャンセルしました。", "warning");
    return;
  } else if (job.status === "error") {
    jobLabel.textContent = "エラー";
    setStatus("エラー", job.message || "");
    setNotice(`処理に失敗しました: ${job.error || job.message || "不明なエラー"}`, "error");
    return;
  }

  if (Array.isArray(job.segments)) {
    renderSegments(job.segments);
  }
  if (job.status === "done") {
    renderResult(job);
  }
}

function renderSegments(segments) {
  if (segments.length < state.lastSegmentCount) {
    segmentBody.innerHTML = "";
    state.lastSegmentCount = 0;
  }
  for (let index = state.lastSegmentCount; index < segments.length; index += 1) {
    const segment = segments[index];
    const row = document.createElement("tr");
    const startCell = document.createElement("td");
    startCell.className = "ts-table__time";
    startCell.textContent = formatClock(segment.start);
    const endCell = document.createElement("td");
    endCell.className = "ts-table__time";
    endCell.textContent = formatClock(segment.end);
    const textCell = document.createElement("td");
    textCell.className = "ts-table__text";
    textCell.textContent = segment.text;
    row.append(startCell, endCell, textCell);
    segmentBody.appendChild(row);
  }
  state.lastSegmentCount = segments.length;
}

function renderResult(job) {
  if (state.lastJobStatus === "done") return;
  state.lastJobStatus = "done";
  resultWrap.hidden = false;

  const languageLabel =
    state.system?.languages.find((item) => item.id === job.language)?.label || job.language || "-";

  const entries = [
    ["言語", `${languageLabel}${job.language_probability ? ` (${Math.round(job.language_probability * 100)}%)` : ""}`],
    ["音声の長さ", formatClock(job.duration || 0)],
    ["処理時間", formatClock(job.elapsed || 0)],
    ["モデル", job.model],
    ["セグメント数", String(job.segment_count || 0)],
  ];
  metaList.innerHTML = "";
  for (const [label, value] of entries) {
    const wrapper = document.createElement("div");
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    wrapper.append(dt, dd);
    metaList.appendChild(wrapper);
  }
  metaList.hidden = false;

  downloads.innerHTML = "";
  for (const fmt of job.outputs || []) {
    const link = document.createElement("a");
    link.className = "ts-download";
    link.href = `/api/jobs/${job.id}/download/${fmt}`;
    link.textContent = fmt.toUpperCase();
    link.setAttribute("download", "");
    downloads.appendChild(link);
  }

  fullText.value = (job.segments || []).map((segment) => segment.text).join("\n");
}

async function cancelJob() {
  if (!state.jobId) return;
  cancelBtn.disabled = true;
  try {
    await apiJson(`/api/jobs/${state.jobId}/cancel`, { method: "POST" });
    setNotice("キャンセルを要求しました。", "warning");
  } catch (error) {
    setNotice(`キャンセルできませんでした: ${error.message}`, "error");
  } finally {
    cancelBtn.disabled = false;
  }
}

async function copyText() {
  const text = fullText.value;
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    setNotice("全文をクリップボードにコピーしました。", "success");
  } catch (error) {
    fullText.removeAttribute("readonly");
    fullText.select();
    document.execCommand("copy");
    fullText.setAttribute("readonly", "");
    setNotice("全文をコピーしました。", "success");
  }
}

startBtn.addEventListener("click", start);
cancelBtn.addEventListener("click", cancelJob);
$("copyBtn").addEventListener("click", copyText);

loadSystem();
