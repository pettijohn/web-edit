import { basicSetup } from "codemirror";
import { EditorView, keymap } from "@codemirror/view";
import { Compartment } from "@codemirror/state";
import { javascript } from "@codemirror/lang-javascript";
import { python } from "@codemirror/lang-python";
import { html } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";

const $ = (id) => document.getElementById(id);
const normalise = (text) => text.replace(/\r\n?/g, "\n");
const parentPath = (path) => path.split("/").slice(0, -1).join("/");
const joinPath = (folder, name) => folder ? `${folder.replace(/\/$/, "")}/${name}` : name;
const routeURL = (mode, path = "") => `#${mode}?path=${encodeURIComponent(path)}`;
const apiURL = (endpoint, path) => `./api/${endpoint}?path=${encodeURIComponent(path)}`;
let currentFolder = "", rootName = "Files", editor = null, editingPath = "", fileVersion = null;
let savedText = "", isNew = false, saving = false, currentHash = "", navigation = 0;
const language = new Compartment();

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function notify(message, error = false) {
  $("notice").textContent = message;
  $("notice").className = error ? "error" : "";
  $("notice").hidden = !message;
}
function confirmAction(title, message, action) {
  const dialog = $("confirmation");
  if (dialog.open) return Promise.resolve(false);
  $("confirmation-title").textContent = title;
  $("confirmation-message").textContent = message;
  $("confirm-action").textContent = action;
  dialog.returnValue = "cancel";
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "accept"), { once: true });
    dialog.showModal();
  });
}
function size(bytes) {
  if (bytes < 1000) return `${bytes} B`;
  if (bytes < 1e6) return `${(bytes / 1000).toFixed(1)} KB`;
  if (bytes < 1e9) return `${(bytes / 1e6).toFixed(1)} MB`;
  return `${(bytes / 1e9).toFixed(1)} GB`;
}
async function api(endpoint, options = {}) {
  const response = await fetch(endpoint, options);
  const data = await response.json();
  if (!response.ok) throw Object.assign(new Error(data.error || `Request failed (${response.status}).`), { status: response.status, data });
  return data;
}
function dirty() { return editor && editor.state.doc.toString() !== savedText; }
function updateSaveState() {
  $("save-state").textContent = saving ? "Saving…" : dirty() ? "Unsaved changes" : isNew ? "New file · not saved yet" : "All changes saved";
  $("save").disabled = saving || (!isNew && !dirty());
}
function languageFor(path) {
  const ext = path.split(".").pop().toLowerCase();
  if (["js", "jsx", "mjs", "cjs", "ts", "tsx"].includes(ext)) return ["JavaScript / TypeScript", javascript({ jsx: ext.endsWith("x"), typescript: ext.startsWith("ts") })];
  if (ext === "py") return ["Python", python()];
  if (["html", "htm"].includes(ext)) return ["HTML", html()];
  if (ext === "css") return ["CSS", css()];
  if (ext === "json") return ["JSON", json()];
  if (["md", "markdown"].includes(ext)) return ["Markdown", markdown()];
  return ["Plain text", []];
}
function showSection(id) {
  for (const section of ["browser", "editor-section", "viewer"]) $(section).hidden = section !== id;
}
function destroyEditor() {
  if (editor) editor.destroy();
  editor = null;
  $("editor").replaceChildren();
}
function breadcrumbs(path) {
  const nav = $("breadcrumbs");
  nav.replaceChildren();
  const root = element("a", "", rootName);
  root.href = routeURL("browse");
  nav.append(root);
  let accumulated = "";
  for (const part of path.split("/").filter(Boolean)) {
    accumulated = joinPath(accumulated, part);
    nav.append(element("span", "", "/"));
    const link = element("a", "", part);
    link.href = routeURL("browse", accumulated);
    nav.append(link);
  }
}
function renderFiles(data) {
  currentFolder = data.path;
  rootName = data.root_name;
  $("root-label").textContent = rootName;
  breadcrumbs(data.path);
  const list = $("file-list");
  list.replaceChildren();
  if (!data.entries.length) list.append(element("div", "empty", "This folder is empty. Create a file or upload something to get started."));
  for (const file of data.entries) {
    const row = element("div", "file-row");
    const supported = ["file", "directory"].includes(file.kind);
    const link = element(supported ? "a" : "span", "file-name");
    if (supported) link.href = routeURL(file.kind === "directory" ? "browse" : file.preview ? "view" : "edit", file.path);
    if (file.preview) {
      const thumbnail = element("img", "thumbnail");
      thumbnail.src = apiURL("image", file.path) + `&v=${file.modified}`;
      thumbnail.alt = "";
      thumbnail.loading = "lazy";
      link.append(thumbnail);
    } else {
      const icon = element("span", `file-icon ${file.kind === "directory" ? "folder-icon" : ""}`, file.kind === "directory" ? "▱" : supported ? "≡" : "↗");
      icon.setAttribute("aria-hidden", "true");
      link.append(icon);
    }
    link.append(element("span", "", file.name + (supported ? "" : " (not followed)")));
    row.append(link, element("span", "file-size", file.kind === "directory" ? "—" : size(file.size)), element("span", "file-date", new Date(file.modified * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })));
    const download = element(file.kind === "file" ? "a" : "span", "file-download", file.kind === "file" ? "Download ↓" : "");
    if (file.kind === "file") { download.href = apiURL("download", file.path); download.download = file.name; download.setAttribute("aria-label", `Download ${file.name}`); }
    row.append(download);
    list.append(row);
  }
}
function openEditor(path, data, newFile) {
  destroyEditor();
  showSection("editor-section");
  editingPath = path; fileVersion = data.version ?? null; isNew = newFile;
  savedText = normalise(data.content);
  $("new-path-label").hidden = !isNew;
  $("new-path").value = path;
  $("editor-title").textContent = isNew ? "New file" : path;
  $("editor-download").hidden = isNew;
  $("editor-download").href = apiURL("download", path);
  const [name, support] = languageFor(path);
  $("language-label").textContent = name;
  editor = new EditorView({
    doc: savedText,
    parent: $("editor"),
    extensions: [
      keymap.of([{ key: "Mod-s", run: () => { save(); return true; } }]),
      basicSetup, language.of(support), EditorView.lineWrapping,
      EditorView.contentAttributes.of({ "aria-label": "File contents", spellcheck: "false", autocapitalize: "off", autocorrect: "off" }),
      EditorView.updateListener.of((update) => { if (update.docChanged) updateSaveState(); }),
    ],
  });
  updateSaveState();
  if (isNew) $("new-path").focus();
}
function openViewer(path, message, preview) {
  destroyEditor();
  showSection("viewer");
  $("viewer-title").textContent = path;
  $("viewer-download").href = apiURL("download", path);
  $("viewer-content").replaceChildren();
  if (preview) {
    const image = element("img");
    image.src = apiURL("image", path); image.alt = path;
    image.addEventListener("error", () => { $("viewer-content").replaceChildren(element("p", "", "This image cannot be previewed. You can download it instead.")); });
    $("viewer-content").append(image);
    if (/\.svg$/i.test(path)) {
      const edit = element("a", "button secondary", "Edit SVG source");
      edit.href = routeURL("edit", path);
      $("viewer-content").append(edit);
    }
  } else $("viewer-content").append(element("p", "", message));
}
async function route() {
  if (saving || (dirty() && !await confirmAction("Unsaved changes", "Discard your unsaved changes and leave this file?", "Discard changes"))) {
    history.replaceState(null, "", currentHash || routeURL("browse", currentFolder));
    return;
  }
  const token = ++navigation;
  currentHash = location.hash;
  const [mode, query = ""] = location.hash.slice(1).split("?");
  const path = new URLSearchParams(query).get("path") || "";
  notify("");
  // Prevent edits to the previous document while a new one is loading.
  destroyEditor();
  showSection("browser");
  $("file-list").replaceChildren(element("div", "empty", "Loading…"));
  try {
    if (mode === "new") { openEditor(path, { content: "" }, true); return; }
    if (mode === "view") { currentFolder = parentPath(path); openViewer(path, "", true); return; }
    if (mode === "edit") {
      currentFolder = parentPath(path);
      let data;
      try { data = await api(apiURL("text", path)); }
      catch (error) {
        if (token !== navigation) return;
        if ([413, 415].includes(error.status)) { openViewer(path, error.message, false); return; }
        throw error;
      }
      if (token !== navigation) return;
      openEditor(path, data, false);
    } else {
      const data = await api(apiURL("browse", path));
      if (token !== navigation) return;
      destroyEditor(); showSection("browser"); renderFiles(data);
    }
  } catch (error) {
    if (token === navigation) {
      $("file-list").replaceChildren(element("div", "empty", "Unable to open this path. Use the folder links above or try Refresh."));
      notify(error.message, true);
    }
  }
}
async function save() {
  if (!editor || saving) return;
  const path = isNew ? $("new-path").value : editingPath;
  if (!path || path.endsWith("/")) { notify("Enter a file path including its filename.", true); $("new-path").focus(); return; }
  const contents = editor.state.doc.toString();
  saving = true; $("new-path").disabled = true; updateSaveState(); notify("");
  try {
    const result = await api("./api/text", { method: "PUT", headers: { "Content-Type": "application/json", "X-Web-Edit": "1" }, body: JSON.stringify({ path, content: contents, version: fileVersion }) });
    savedText = contents; fileVersion = result.version; editingPath = path; isNew = false;
    currentFolder = parentPath(path);
    currentHash = routeURL("edit", path); history.replaceState(null, "", currentHash);
    $("editor-title").textContent = path; $("new-path-label").hidden = true;
    $("editor-download").hidden = false; $("editor-download").href = apiURL("download", path);
    notify(`Saved ${path}.`);
  } catch (error) {
    notify(error.status === 409 ? `${error.message} Your edits are still here. ${isNew ? "Choose a different path." : "Copy your edits before reopening the file to load the latest version."}` : error.message, true);
  } finally { saving = false; $("new-path").disabled = false; updateSaveState(); }
}

const queue = [];
let activeUploads = 0;
function updateQueue() {
  const completed = queue.filter((item) => item.state === "done").length;
  const waiting = queue.filter((item) => item.state === "queued").length;
  $("upload-summary").textContent = queue.length ? `${completed} of ${queue.length} uploaded${activeUploads ? ` · ${activeUploads} uploading` : ""}` : "";
  $("start-uploads").disabled = !waiting;
}
function updateItem(item) {
  item.input.disabled = ["uploading", "done", "pending"].includes(item.state);
  item.progress.value = item.percent;
  item.status.textContent = item.message;
  item.controls.replaceChildren();
  if (item.state === "error") {
    const retry = element("button", "secondary", item.conflict ? "Replace existing" : "Retry");
    retry.addEventListener("click", async () => {
      if (item.conflict && !await confirmAction("Replace existing file", `Replace the existing file at ${item.input.value}?`, "Replace file")) return;
      item.version = item.conflict?.version;
      item.state = "pending"; item.message = "Waiting…"; updateItem(item); pump();
    });
    item.controls.append(retry);
  }
  if (!["done", "pending"].includes(item.state)) {
    const remove = element("button", "secondary", item.state === "uploading" ? "Cancel" : "Remove");
    remove.addEventListener("click", () => {
      if (item.state === "uploading") item.xhr.abort();
      else { queue.splice(queue.indexOf(item), 1); item.row.remove(); updateQueue(); }
    });
    item.controls.append(remove);
  }
  updateQueue();
}
function addFiles(files) {
  for (const file of files) {
    const item = { file, state: "queued", percent: 0, message: `${size(file.size)} · Ready to upload`, version: undefined, conflict: null };
    item.row = element("div", "upload-item");
    const label = element("label", "", `Destination for ${file.name}`);
    item.input = element("input"); item.input.value = joinPath($("upload-folder").value, file.name); item.input.spellcheck = false;
    item.input.addEventListener("input", () => { item.version = undefined; item.conflict = null; item.state = "queued"; item.message = `${size(file.size)} · Ready to upload`; updateItem(item); });
    label.append(item.input);
    const info = element("div", "upload-info");
    item.status = element("span", "upload-status"); item.status.setAttribute("role", "status");
    item.controls = element("div", "upload-controls"); info.append(item.status, item.controls);
    item.progress = element("progress"); item.progress.max = 100; item.progress.value = 0; item.progress.setAttribute("aria-label", `Upload progress for ${file.name}`);
    item.row.append(label, info, item.progress); $("upload-list").append(item.row);
    queue.push(item); updateItem(item);
  }
}
function uploadFile(item) {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest(); item.xhr = xhr;
    xhr.open("POST", "./api/upload"); xhr.setRequestHeader("X-Web-Edit", "1");
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) {
        item.percent = Math.round(event.loaded / event.total * 100);
        item.message = item.percent === 100 ? "Saving on server…" : `${item.percent}% · ${size(item.file.size)}`;
        item.progress.value = item.percent; item.status.textContent = item.message;
      }
    });
    const fail = (message) => { item.state = "error"; item.message = message; };
    xhr.addEventListener("load", () => {
      let data;
      try { data = JSON.parse(xhr.responseText); } catch { data = { error: `Upload failed (${xhr.status}).` }; }
      if (xhr.status >= 200 && xhr.status < 300) { item.state = "done"; item.percent = 100; item.message = `${size(item.file.size)} · Uploaded`; item.conflict = null; }
      else { fail(data.error || "Upload failed."); item.conflict = xhr.status === 409 && data.version ? data : null; }
    });
    xhr.addEventListener("error", () => fail("Connection failed. Retry this file."));
    xhr.addEventListener("abort", () => fail("Upload cancelled. You can retry."));
    xhr.addEventListener("loadend", resolve);
    const form = new FormData(); form.append("file", item.file); form.append("path", item.input.value);
    if (item.version) form.append("version", item.version);
    xhr.send(form);
  });
}
function pump() {
  while (activeUploads < 3) {
    const item = queue.find((candidate) => candidate.state === "pending");
    if (!item) break;
    item.state = "uploading"; item.message = "Uploading…"; item.percent = 0; activeUploads++; updateItem(item);
    uploadFile(item).finally(async () => {
      activeUploads--; updateItem(item); pump();
      if (!activeUploads && !$("browser").hidden) {
        const folder = currentFolder;
        try { const data = await api(apiURL("browse", folder)); if (currentFolder === folder && !$("browser").hidden) renderFiles(data); } catch (error) { notify(error.message, true); }
      }
    });
  }
  updateQueue();
}
$("start-uploads").addEventListener("click", () => { for (const item of queue) if (item.state === "queued") { item.state = "pending"; item.message = "Waiting…"; updateItem(item); } pump(); });
$("show-upload").addEventListener("click", () => { $("uploads").hidden = false; $("upload-folder").value = currentFolder; $("uploads").scrollIntoView({ behavior: "smooth", block: "start" }); });
$("close-uploads").addEventListener("click", () => { $("uploads").hidden = true; });
$("file-picker").addEventListener("change", (event) => { addFiles(event.target.files); event.target.value = ""; });
const drop = $("drop-zone");
for (const name of ["dragenter", "dragover"]) drop.addEventListener(name, (event) => { event.preventDefault(); drop.classList.add("dragging"); });
for (const name of ["dragleave", "drop"]) drop.addEventListener(name, (event) => { event.preventDefault(); drop.classList.remove("dragging"); });
drop.addEventListener("drop", (event) => addFiles(event.dataTransfer.files));
$("new-file").addEventListener("click", () => { location.hash = routeURL("new", currentFolder ? `${currentFolder}/` : ""); });
$("refresh").addEventListener("click", route);
for (const id of ["back", "viewer-back"]) $(id).addEventListener("click", () => { location.hash = routeURL("browse", currentFolder); });
$("save").addEventListener("click", save);
$("new-path").addEventListener("input", () => { if (editor) { const [name, support] = languageFor($("new-path").value); $("language-label").textContent = name; editor.dispatch({ effects: language.reconfigure(support) }); } });
window.addEventListener("hashchange", route);
window.addEventListener("beforeunload", (event) => { if (dirty() || saving || activeUploads) { event.preventDefault(); event.returnValue = ""; } });
document.addEventListener("keydown", (event) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s" && editor) { event.preventDefault(); save(); } });
route();
