"use strict";

const runToken = new URLSearchParams(window.location.search).get("token") || "";

const state = {
  bootstrap: null,
  profiles: null,
  templates: null,
  library: [],
  librarySelected: null,
  creators: {},
};

function buildUrl(path, params = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([key, value]) => url.searchParams.set(key, String(value)));
  return url.pathname + url.search;
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-Depth-Token", runToken);
  let body = options.body;
  if (options.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.json);
  }
  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    body,
    cache: "no-store",
  });
  let data;
  try {
    data = await response.json();
  } catch (_error) {
    throw new Error(`The local server returned an unreadable response (${response.status}).`);
  }
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `Request failed (${response.status}).`);
  }
  return data;
}

function toast(message, kind = "normal") {
  const region = document.getElementById("toast-region");
  const item = document.createElement("div");
  item.className = `toast ${kind === "error" ? "error" : ""}`;
  item.textContent = message;
  region.append(item);
  window.setTimeout(() => item.remove(), kind === "error" ? 6500 : 3300);
}

function slugify(value) {
  return value
    .trim()
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 64);
}

function newDraftId() {
  return crypto.randomUUID();
}

function validDraftId(value) {
  return /^[A-Za-z0-9_-]{8,80}$/.test(value || "");
}

function safeStoredDraft(profileKey) {
  const key = `dhsa-draft-${profileKey}`;
  let value = localStorage.getItem(key);
  if (!validDraftId(value)) {
    value = newDraftId();
    localStorage.setItem(key, value);
  }
  return value;
}

function saveCreatorForm(creator) {
  const data = {
    name: creator.nameInput.value,
    slug: creator.slugInput.value,
    description: creator.descriptionInput.value,
    slugTouched: creator.slugTouched,
  };
  localStorage.setItem(`dhsa-form-${creator.profileKey}`, JSON.stringify(data));
}

function loadCreatorForm(profileKey) {
  try {
    const value = JSON.parse(localStorage.getItem(`dhsa-form-${profileKey}`) || "{}");
    return value && typeof value === "object" ? value : {};
  } catch (_error) {
    return {};
  }
}

function fillTemplate(template, values) {
  return template.replace(/\{\{([a-z_]+)\}\}/gi, (_match, key) => values[key] ?? `{{${key}}}`);
}

function mediaUrl(relativePath) {
  return buildUrl("/media", { path: relativePath, token: runToken });
}

function draftMediaUrl(draftId, storedName) {
  return buildUrl("/draft-media", { draft: draftId, file: storedName, token: runToken });
}

const activeAnimations = new Set();
const visibilityObserver = "IntersectionObserver" in window
  ? new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.target.__spriteAnimation) {
          entry.target.__spriteAnimation.visible = entry.isIntersecting;
        }
      });
    }, { rootMargin: "100px" })
  : null;

function drawAnimationFrame(animation, frameIndex) {
  const { canvas, image, frameWidth, frameHeight } = animation;
  if (!image.complete || !image.naturalWidth) return;
  const context = canvas.getContext("2d");
  context.imageSmoothingEnabled = false;
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.drawImage(
    image,
    frameIndex * frameWidth,
    0,
    frameWidth,
    frameHeight,
    0,
    0,
    canvas.width,
    canvas.height,
  );
}

function animationLoop(now) {
  activeAnimations.forEach((animation) => {
    if (!animation.canvas.isConnected) {
      activeAnimations.delete(animation);
      return;
    }
    if (!animation.loaded || !animation.visible) return;
    if (animation.frames > 1 && now - animation.lastFrame >= animation.frameMs) {
      animation.frame = (animation.frame + 1) % animation.frames;
      animation.lastFrame = now;
      drawAnimationFrame(animation, animation.frame);
    }
  });
  requestAnimationFrame(animationLoop);
}
requestAnimationFrame(animationLoop);

function addSpritePreview(container, asset, url) {
  const frames = Math.max(1, Number(asset.frames || 1));
  const width = Number(asset.width || asset.frame_width || 32);
  const height = Number(asset.height || asset.frame_height || 32);
  const frameWidth = Number(asset.frame_width || (width % frames === 0 ? width / frames : width));
  const frameHeight = Number(asset.frame_height || height);
  const largeStatic = frames === 1 && (frameWidth > 64 || frameHeight > 64 || /_large\.png$/i.test(asset.name || ""));

  if (largeStatic) {
    const image = document.createElement("img");
    image.src = url;
    image.alt = asset.name || "Sprite preview";
    image.loading = "lazy";
    container.append(image);
    return;
  }

  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, frameWidth);
  canvas.height = Math.max(1, frameHeight);
  canvas.setAttribute("aria-label", asset.name || "Animated sprite preview");
  container.append(canvas);
  const image = new Image();
  image.decoding = "async";
  const animation = {
    canvas,
    image,
    frames,
    frameWidth,
    frameHeight,
    frameMs: Math.max(60, Number(asset.frame_ms || 170)),
    frame: 0,
    lastFrame: performance.now(),
    loaded: false,
    visible: true,
  };
  canvas.__spriteAnimation = animation;
  image.addEventListener("load", () => {
    animation.loaded = true;
    drawAnimationFrame(animation, 0);
  });
  image.addEventListener("error", () => {
    container.textContent = "Preview failed to load";
  });
  image.src = url;
  activeAnimations.add(animation);
  if (visibilityObserver) visibilityObserver.observe(canvas);
}

function setActiveTab(tab) {
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tab);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.panel === tab);
  });
  history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${tab}`);
}

function setupTabs() {
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.addEventListener("click", () => setActiveTab(button.dataset.tab));
  });
  const requested = window.location.hash.replace(/^#/, "");
  if (document.querySelector(`[data-panel="${CSS.escape(requested)}"]`)) setActiveTab(requested);
}

function updateRootDisplay() {
  const root = state.bootstrap.root;
  const pill = document.getElementById("root-pill");
  const text = document.getElementById("root-pill-text");
  pill.classList.toggle("ready", root.available);
  pill.classList.toggle("error", !root.available);
  text.textContent = root.available ? root.path : "Sprites root not found — open Settings";
  document.getElementById("settings-root").value = root.path || "";
  document.getElementById("settings-root-result").textContent = root.available
    ? `Connected (${root.source}): ${root.path}`
    : "The player and monsters folders have not been located yet.";
  Object.values(state.creators).forEach(updateCreatorProgress);
}

function groupExpectation(group) {
  const outputCount = group.outputs.length;
  if (group.mode === "passthrough") {
    return `Upload one transparent PNG → ${outputCount} examine image`;
  }
  const outputText = outputCount === 1 ? "one finished PNG" : `${outputCount} finished directional PNGs`;
  return `Upload exactly ${group.source_frames} separate sprite${group.source_frames === 1 ? "" : "s"} → ${outputText}`;
}

function buildPrompt(profile, group, name, description) {
  const template = state.templates.templates[group.prompt_key] || "";
  return fillTemplate(template, {
    name: name.trim() || "[name]",
    description: description.trim() || "[description]",
    subject: profile.subject,
    style: state.templates.style,
    reference: state.templates.reference,
  });
}

function renderCreation(profileKey) {
  const profile = state.profiles.profiles[profileKey];
  const host = document.querySelector(`[data-profile-host="${profileKey}"]`);
  const fragment = document.getElementById("creation-template").content.cloneNode(true);
  host.replaceChildren(fragment);
  const page = host.querySelector(".creation-page");
  page.querySelector(".creation-title").textContent = `Create a new ${profile.label.toLowerCase()}`;
  page.querySelector(".creation-notes").textContent = profile.notes;
  page.querySelector(".creation-eyebrow").textContent = profileKey === "mob" ? "Monster production" : "Player production";

  const storedForm = loadCreatorForm(profileKey);
  const creator = {
    profileKey,
    profile,
    page,
    draftId: safeStoredDraft(profileKey),
    draftStatus: { groups: {}, missing_groups: profile.groups.map((group) => group.id), ready: false },
    cards: new Map(),
    nameInput: page.querySelector(".character-name"),
    slugInput: page.querySelector(".folder-slug"),
    descriptionInput: page.querySelector(".character-description"),
    slugTouched: Boolean(storedForm.slugTouched),
  };
  state.creators[profileKey] = creator;
  creator.nameInput.value = storedForm.name || "";
  creator.slugInput.value = storedForm.slug || "";
  creator.descriptionInput.value = storedForm.description || "";
  page.querySelector(".draft-id").textContent = `Draft ${creator.draftId}`;

  const grid = page.querySelector(".prompt-upload-grid");
  profile.groups.forEach((group, index) => {
    const cardFragment = document.getElementById("upload-card-template").content.cloneNode(true);
    const card = cardFragment.querySelector(".upload-card");
    card.dataset.group = group.id;
    card.querySelector(".group-number").textContent = `Upload ${index + 1} of ${profile.groups.length}`;
    card.querySelector(".group-label").textContent = group.label;
    card.querySelector(".group-expectation").textContent = groupExpectation(group);
    const promptArea = card.querySelector(".generated-prompt");
    promptArea.value = buildPrompt(profile, group, creator.nameInput.value, creator.descriptionInput.value);
    card.querySelector(".copy-prompt").addEventListener("click", () => copyText(promptArea.value));
    const input = card.querySelector(".group-upload");
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      if (file) uploadGroup(creator, group, file);
      input.value = "";
    });
    creator.cards.set(group.id, { card, group, promptArea, input });
    grid.append(cardFragment);
  });

  creator.nameInput.addEventListener("input", () => {
    if (!creator.slugTouched) creator.slugInput.value = slugify(creator.nameInput.value);
    saveCreatorForm(creator);
    updateCreatorProgress(creator);
    renderCreatorDraft(creator);
  });
  creator.slugInput.addEventListener("input", () => {
    creator.slugInput.value = slugify(creator.slugInput.value);
    creator.slugTouched = true;
    saveCreatorForm(creator);
    updateCreatorProgress(creator);
    renderCreatorDraft(creator);
  });
  creator.descriptionInput.addEventListener("input", () => {
    saveCreatorForm(creator);
    updateCreatorProgress(creator);
  });
  page.querySelector(".generate-prompts").addEventListener("click", () => generateCreatorPrompts(creator));
  page.querySelector(".reset-draft").addEventListener("click", () => resetCreatorDraft(creator));
  page.querySelector(".export-button").addEventListener("click", () => exportCreator(creator));
  updateCreatorProgress(creator);
}

function generateCreatorPrompts(creator) {
  const name = creator.nameInput.value.trim();
  const description = creator.descriptionInput.value.trim();
  if (!name || !description) {
    toast("Add both a name and description before generating prompts.", "error");
    return;
  }
  creator.cards.forEach(({ group, promptArea }) => {
    promptArea.value = buildPrompt(creator.profile, group, name, description);
  });
  saveCreatorForm(creator);
  toast("All prompts refreshed.");
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (_error) {
    const helper = document.createElement("textarea");
    helper.value = text;
    document.body.append(helper);
    helper.select();
    document.execCommand("copy");
    helper.remove();
  }
  toast("Prompt copied.");
}

async function loadCreatorDraft(creator) {
  try {
    const result = await api(buildUrl("/api/draft", { draft: creator.draftId, profile: creator.profileKey }));
    creator.draftStatus = result;
    renderCreatorDraft(creator);
  } catch (error) {
    toast(`Could not load ${creator.profile.label.toLowerCase()} draft: ${error.message}`, "error");
  }
}

async function uploadGroup(creator, group, file) {
  const view = creator.cards.get(group.id);
  if (!/\.png$/i.test(file.name)) {
    toast("Choose a PNG file.", "error");
    return;
  }
  view.input.disabled = true;
  view.card.classList.remove("ready", "error");
  view.card.querySelector(".completion-badge").textContent = "Processing";
  const message = view.card.querySelector(".upload-message");
  message.className = "upload-message";
  message.textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MB · running normalizer…`;
  try {
    const result = await api(buildUrl("/api/upload", {
      draft: creator.draftId,
      profile: creator.profileKey,
      group: group.id,
    }), {
      method: "POST",
      headers: { "Content-Type": "image/png" },
      body: file,
    });
    creator.draftStatus = result;
    renderCreatorDraft(creator);
    toast(`${group.label} is ready.`);
  } catch (error) {
    view.card.classList.add("error");
    view.card.querySelector(".completion-badge").textContent = "Needs attention";
    message.className = "upload-message error-text";
    message.textContent = error.message;
    toast(error.message, "error");
  } finally {
    view.input.disabled = false;
  }
}

function renderCreatorDraft(creator) {
  const savedGroups = creator.draftStatus.groups || {};
  creator.cards.forEach((view, groupId) => {
    const saved = savedGroups[groupId];
    const badge = view.card.querySelector(".completion-badge");
    const message = view.card.querySelector(".upload-message");
    const output = view.card.querySelector(".processed-output");
    view.card.classList.remove("ready", "error");
    output.replaceChildren();
    if (saved && saved.ready) {
      view.card.classList.add("ready");
      badge.textContent = "Ready";
      message.className = "upload-message";
      const warningText = (saved.warnings || []).join(" ");
      message.textContent = `Source ${saved.source_width}x${saved.source_height} processed successfully.${warningText ? ` ${warningText}` : ""}`;
      saved.outputs.forEach((record) => {
        const item = document.createElement("div");
        item.className = "processed-item";
        const preview = document.createElement("div");
        preview.className = "asset-preview";
        const finalName = `${creator.slugInput.value || "name"}${record.suffix}`;
        addSpritePreview(preview, { ...record, name: finalName, frame_width: record.width / record.frames, frame_height: record.height }, draftMediaUrl(creator.draftId, record.stored_name));
        const name = document.createElement("div");
        name.className = "processed-name";
        name.textContent = `${finalName} · ${record.width}x${record.height}`;
        item.append(preview, name);
        output.append(item);
      });
    } else if (saved && saved.error) {
      view.card.classList.add("error");
      badge.textContent = "Needs attention";
      message.className = "upload-message error-text";
      message.textContent = saved.error;
    } else {
      badge.textContent = "Waiting";
      message.className = "upload-message";
      message.textContent = "";
    }
  });
  updateCreatorProgress(creator);
}

function creatorIdentityValid(creator) {
  return Boolean(
    creator.nameInput.value.trim()
    && creator.descriptionInput.value.trim()
    && /^[a-z0-9][a-z0-9_-]{0,63}$/.test(creator.slugInput.value.trim()),
  );
}

function updateCreatorProgress(creator) {
  if (!creator || !creator.page) return;
  const groups = creator.draftStatus.groups || {};
  const readyCount = creator.profile.groups.filter((group) => groups[group.id] && groups[group.id].ready).length;
  const total = creator.profile.groups.length;
  const percent = total ? Math.round((readyCount / total) * 100) : 0;
  creator.page.querySelector(".progress-label").textContent = `${readyCount} of ${total} uploads ready`;
  creator.page.querySelector(".progress-fill").style.width = `${percent}%`;
  creator.page.querySelector(".progress-detail").textContent = readyCount === total
    ? "Every image group has passed initial processing."
    : `${total - readyCount} required upload${total - readyCount === 1 ? "" : "s"} remaining.`;
  const ready = readyCount === total && creatorIdentityValid(creator) && Boolean(state.bootstrap.root.available);
  const exportButton = creator.page.querySelector(".export-button");
  exportButton.disabled = !ready;
  const summary = creator.page.querySelector(".export-summary");
  if (!state.bootstrap.root.available) {
    summary.textContent = "Connect the sprites root in Settings before exporting.";
  } else if (!creatorIdentityValid(creator)) {
    summary.textContent = "Enter a name, description and valid lowercase folder name.";
  } else if (readyCount !== total) {
    summary.textContent = `Upload every required image to unlock export (${readyCount}/${total} groups ready).`;
  } else {
    summary.textContent = `Ready to create ${creator.profile.folder}\\${creator.slugInput.value} after one final validation pass.`;
  }
}

async function resetCreatorDraft(creator) {
  if (!window.confirm("Clear the uploaded images in this draft and start a fresh one?")) return;
  try {
    await api("/api/draft/reset", { method: "POST", json: { draft: creator.draftId } });
  } catch (error) {
    toast(error.message, "error");
    return;
  }
  creator.draftId = newDraftId();
  localStorage.setItem(`dhsa-draft-${creator.profileKey}`, creator.draftId);
  creator.page.querySelector(".draft-id").textContent = `Draft ${creator.draftId}`;
  creator.draftStatus = { groups: {}, missing_groups: creator.profile.groups.map((group) => group.id), ready: false };
  creator.page.querySelector(".export-result").classList.add("hidden");
  renderCreatorDraft(creator);
  toast("Fresh draft started.");
}

async function exportCreator(creator) {
  if (!creatorIdentityValid(creator)) {
    toast("Complete the name, description and folder name first.", "error");
    return;
  }
  const button = creator.page.querySelector(".export-button");
  const resultBox = creator.page.querySelector(".export-result");
  button.disabled = true;
  button.textContent = "Validating and exporting…";
  try {
    const result = await api("/api/export", {
      method: "POST",
      json: { draft: creator.draftId, profile: creator.profileKey, slug: creator.slugInput.value.trim() },
    });
    resultBox.classList.remove("hidden");
    resultBox.textContent = `Created ${result.relative_path} with ${result.file_count} PNGs. ${result.note}`;
    toast(`Exported ${result.relative_path}.`);
    loadLibrary();
  } catch (error) {
    resultBox.classList.add("hidden");
    toast(error.message, "error");
  } finally {
    button.textContent = "Export to folder";
    updateCreatorProgress(creator);
  }
}

async function loadLibrary() {
  const button = document.getElementById("refresh-library");
  button.disabled = true;
  button.textContent = "Scanning…";
  try {
    const result = await api("/api/library");
    state.library = result.entities || [];
    const notice = document.getElementById("library-notice");
    if (!result.available || (result.errors || []).length) {
      notice.classList.remove("hidden");
      notice.textContent = (result.errors || ["Sprites root is unavailable."]).join(" ");
    } else {
      notice.classList.add("hidden");
    }
    renderEntityList();
  } catch (error) {
    document.getElementById("library-notice").classList.remove("hidden");
    document.getElementById("library-notice").textContent = error.message;
    toast(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Refresh library";
  }
}

function filteredEntities() {
  const search = document.getElementById("library-search").value.trim().toLowerCase();
  const profile = document.getElementById("library-profile-filter").value;
  return state.library.filter((entity) => {
    const profileMatch = profile === "all" || entity.profile === profile;
    const searchMatch = !search || entity.name.toLowerCase().includes(search) || entity.relative_path.toLowerCase().includes(search);
    return profileMatch && searchMatch;
  });
}

function renderEntityList() {
  const entities = filteredEntities();
  const list = document.getElementById("entity-list");
  list.replaceChildren();
  if (!entities.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state compact";
    empty.textContent = state.library.length ? "No matching folders." : "No player or monster subfolders found.";
    list.append(empty);
    document.getElementById("library-detail").innerHTML = '<div class="empty-state">Nothing to display.</div>';
    return;
  }
  if (!entities.some((entity) => entity.relative_path === state.librarySelected)) {
    state.librarySelected = entities[0].relative_path;
  }
  entities.forEach((entity) => {
    const button = document.createElement("button");
    button.className = `entity-button ${entity.relative_path === state.librarySelected ? "active" : ""}`;
    const name = document.createElement("strong");
    name.textContent = entity.name;
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = String(entity.asset_count);
    const type = document.createElement("span");
    type.className = "type";
    type.textContent = entity.profile_label;
    button.append(name, count, type);
    button.addEventListener("click", () => {
      state.librarySelected = entity.relative_path;
      renderEntityList();
    });
    list.append(button);
  });
  renderLibraryDetail(entities.find((entity) => entity.relative_path === state.librarySelected));
}

function renderLibraryDetail(entity) {
  const detail = document.getElementById("library-detail");
  detail.replaceChildren();
  if (!entity) return;
  const header = document.createElement("div");
  header.className = "detail-header";
  const heading = document.createElement("h3");
  heading.textContent = entity.name;
  const path = document.createElement("p");
  path.textContent = `${entity.relative_path} · ${entity.asset_count} PNG${entity.asset_count === 1 ? "" : "s"}`;
  header.append(heading, path);
  detail.append(header);
  const grid = document.createElement("div");
  grid.className = "asset-grid";
  if (!entity.assets.length) {
    grid.innerHTML = '<div class="empty-state">This folder contains no PNG files.</div>';
  }
  entity.assets.forEach((asset) => {
    const card = document.createElement("article");
    card.className = "asset-card";
    const preview = document.createElement("div");
    preview.className = "asset-preview";
    if (asset.error) {
      preview.textContent = asset.error;
    } else {
      addSpritePreview(preview, asset, mediaUrl(asset.relative_path));
    }
    const meta = document.createElement("div");
    meta.className = "asset-meta";
    const name = document.createElement("strong");
    name.textContent = asset.name;
    const dimensions = document.createElement("span");
    dimensions.textContent = asset.error ? "Unreadable image" : `${asset.width}x${asset.height} · ${asset.frames} frame${asset.frames === 1 ? "" : "s"}`;
    meta.append(name, dimensions);
    (asset.warnings || []).forEach((warning) => {
      const line = document.createElement("span");
      line.className = "asset-warning";
      line.textContent = warning;
      meta.append(line);
    });
    card.append(preview, meta);
    grid.append(card);
  });
  detail.append(grid);
}

function renderTemplateEditor() {
  document.getElementById("template-style").value = state.templates.style || "";
  document.getElementById("template-reference").value = state.templates.reference || "";
  const list = document.getElementById("template-list");
  list.replaceChildren();
  const labels = new Map();
  Object.values(state.profiles.profiles).forEach((profile) => {
    profile.groups.forEach((group) => labels.set(group.prompt_key, group.label));
  });
  Object.keys(state.templates.templates).forEach((key) => {
    const label = document.createElement("label");
    label.className = "field-card";
    const title = document.createElement("span");
    title.className = "field-title";
    title.textContent = labels.get(key) || key;
    const help = document.createElement("span");
    help.className = "field-help";
    help.textContent = `Template key: ${key}`;
    const textarea = document.createElement("textarea");
    textarea.rows = 9;
    textarea.dataset.templateKey = key;
    textarea.value = state.templates.templates[key];
    label.append(title, help, textarea);
    list.append(label);
  });
}

async function saveTemplates() {
  const templates = {};
  document.querySelectorAll("[data-template-key]").forEach((textarea) => {
    templates[textarea.dataset.templateKey] = textarea.value;
  });
  try {
    const result = await api("/api/templates", {
      method: "POST",
      json: {
        version: 1,
        style: document.getElementById("template-style").value,
        reference: document.getElementById("template-reference").value,
        templates,
      },
    });
    state.templates = result.templates;
    toast("Prompt templates saved.");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function resetTemplates() {
  if (!window.confirm("Restore every prompt template to the supplied defaults?")) return;
  try {
    const result = await api("/api/templates/reset", { method: "POST", json: {} });
    state.templates = result.templates;
    renderTemplateEditor();
    toast("Default prompts restored.");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function saveRoot() {
  const path = document.getElementById("settings-root").value.trim();
  try {
    const result = await api("/api/settings/root", { method: "POST", json: { path } });
    state.bootstrap.root = result.root;
    updateRootDisplay();
    await loadLibrary();
    toast("Sprites root saved.");
  } catch (error) {
    document.getElementById("settings-root-result").textContent = error.message;
    toast(error.message, "error");
  }
}

function wireGlobalControls() {
  document.getElementById("refresh-library").addEventListener("click", loadLibrary);
  document.getElementById("library-search").addEventListener("input", renderEntityList);
  document.getElementById("library-profile-filter").addEventListener("change", renderEntityList);
  document.getElementById("save-templates").addEventListener("click", saveTemplates);
  document.getElementById("reset-templates").addEventListener("click", resetTemplates);
  document.getElementById("save-root").addEventListener("click", saveRoot);
  document.getElementById("quit-app").addEventListener("click", async () => {
    if (!window.confirm("Stop the local Spritesheet Assistant server?")) return;
    try {
      await api("/api/shutdown", { method: "POST", json: {} });
      document.body.innerHTML = '<div class="empty-state"><div><h1>Spritesheet Assistant stopped</h1><p>You may close this browser tab.</p></div></div>';
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

async function initialise() {
  setupTabs();
  if (!runToken) {
    throw new Error("This page is missing its launch token. Start it with run_spritesheet_assistant.bat.");
  }
  const bootstrap = await api("/api/bootstrap");
  state.bootstrap = bootstrap;
  state.profiles = bootstrap.profiles;
  state.templates = bootstrap.templates;
  updateRootDisplay();
  renderCreation("mob");
  renderCreation("player");
  renderTemplateEditor();
  wireGlobalControls();
  updateRootDisplay();
  await Promise.all([
    loadCreatorDraft(state.creators.mob),
    loadCreatorDraft(state.creators.player),
    loadLibrary(),
  ]);
}

initialise().catch((error) => {
  console.error(error);
  document.querySelector("main").innerHTML = `
    <div class="empty-state">
      <div>
        <h2>Could not start the Spritesheet Assistant</h2>
        <p></p>
      </div>
    </div>`;
  document.querySelector("main p").textContent = error.message;
  const pill = document.getElementById("root-pill");
  pill.classList.add("error");
  document.getElementById("root-pill-text").textContent = "Connection failed";
});
