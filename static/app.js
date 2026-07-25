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

function draftRawUrl(creator, groupId, rawVersion = "") {
  return buildUrl("/draft-raw", {
    draft: creator.draftId,
    profile: creator.profileKey,
    group: groupId,
    version: rawVersion,
    token: runToken,
  });
}

function creatorIdentity(creator) {
  return {
    draft: creator.draftId,
    profile: creator.profileKey,
    name: creator.nameInput.value.trim(),
    slug: creator.slugInput.value.trim(),
    description: creator.descriptionInput.value.trim(),
  };
}

function scheduleIdentitySave(creator) {
  window.clearTimeout(creator.identitySaveTimer);
  creator.identitySaveTimer = window.setTimeout(() => persistIdentity(creator), 350);
}

async function persistIdentity(creator) {
  window.clearTimeout(creator.identitySaveTimer);
  try {
    const result = await api("/api/draft/identity", { method: "POST", json: creatorIdentity(creator) });
    creator.draftStatus.identity = result.identity;
  } catch (error) {
    toast(`Could not save draft details: ${error.message}`, "error");
  }
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

function addSpritePreviewComparison(container, asset, url) {
  const frames = Math.max(1, Number(asset.frames || 1));
  const width = Number(asset.width || asset.frame_width || 32);
  const height = Number(asset.height || asset.frame_height || 32);
  const frameWidth = Number(asset.frame_width || (width % frames === 0 ? width / frames : width));
  const frameHeight = Number(asset.frame_height || height);
  if (frameWidth !== 32 || frameHeight !== 32) {
    addSpritePreview(container, asset, url);
    return;
  }

  container.classList.add("preview-comparison");
  const enlarged = document.createElement("div");
  enlarged.className = "preview-stage enlarged";
  const enlargedLabel = document.createElement("span");
  enlargedLabel.className = "preview-stage-label";
  enlargedLabel.textContent = "Enlarged preview";
  const enlargedMedia = document.createElement("div");
  enlargedMedia.className = "preview-media";
  enlarged.append(enlargedMedia, enlargedLabel);

  const actual = document.createElement("div");
  actual.className = "preview-stage actual";
  const actualLabel = document.createElement("span");
  actualLabel.className = "preview-stage-label";
  actualLabel.textContent = "32×32 in game";
  const actualMedia = document.createElement("div");
  actualMedia.className = "preview-media";
  actual.append(actualMedia, actualLabel);

  container.append(enlarged, actual);
  addSpritePreview(enlargedMedia, asset, url);
  addSpritePreview(actualMedia, asset, url);
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
  if (group.mode === "crop_pair") {
    return "Upload one image at any size → crop once → 32x32 sprite + 256x256 examine image";
  }
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

function detectionSettings(view) {
  return {
    mode: view.card.querySelector(".detect-mode").value,
    columns: Number(view.card.querySelector(".detect-columns").value),
    rows: Number(view.card.querySelector(".detect-rows").value),
    alpha_threshold: Number(view.card.querySelector(".detect-alpha").value),
    min_blob_area: Number(view.card.querySelector(".detect-area").value),
    row_tolerance: Number(view.card.querySelector(".detect-row").value),
  };
}

function setDetectionControls(view, settings = null) {
  if (view.group.mode === "crop_pair" || view.group.mode === "passthrough") return;
  const hint = view.group.grid_hint || [view.group.source_frames, 1];
  const defaults = state.bootstrap.detection_defaults || {};
  const value = settings || {
    mode: "auto",
    columns: hint[0],
    rows: hint[1],
    alpha_threshold: defaults.alpha_threshold ?? 0,
    min_blob_area: defaults.min_blob_area ?? 64,
    row_tolerance: defaults.row_tolerance ?? 12,
  };
  view.card.querySelector(".detect-mode").value = value.mode || "auto";
  view.card.querySelector(".detect-columns").value = value.columns ?? hint[0];
  view.card.querySelector(".detect-rows").value = value.rows ?? hint[1];
  view.card.querySelector(".detect-alpha").value = value.alpha_threshold ?? 0;
  view.card.querySelector(".detect-area").value = value.min_blob_area ?? 64;
  view.card.querySelector(".detect-row").value = value.row_tolerance ?? 12;
}

const QUALITY_KEYS = [
  "downscale_filter",
  "preblur",
  "sharpen",
  "quantize",
  "colour_count",
  "alpha_aware",
  "gamma_correct",
  "alpha_cleanup",
  "hard_alpha",
  "despeckle",
];

function qualityPresetValues() {
  const defaults = state.bootstrap.quality_defaults || {};
  return {
    balanced: {
      ...defaults,
      downscale_filter: defaults.downscale_filter || "auto",
      preblur: Number(defaults.preblur ?? 0.15),
      sharpen: Number(defaults.sharpen ?? 1.45),
      quantize: defaults.quantize ?? true,
      colour_count: Number(defaults.colour_count ?? 48),
      alpha_aware: defaults.alpha_aware ?? true,
      gamma_correct: defaults.gamma_correct ?? true,
      alpha_cleanup: defaults.alpha_cleanup ?? true,
      hard_alpha: defaults.hard_alpha ?? false,
      despeckle: defaults.despeckle ?? false,
    },
    crisp: {
      ...defaults,
      downscale_filter: "lanczos",
      preblur: 0,
      sharpen: 1.9,
      quantize: true,
      colour_count: 40,
      alpha_aware: true,
      gamma_correct: true,
      alpha_cleanup: true,
      hard_alpha: false,
      despeckle: false,
    },
    hard: {
      ...defaults,
      downscale_filter: "nearest",
      preblur: 0,
      sharpen: 1,
      quantize: true,
      colour_count: 32,
      alpha_aware: true,
      gamma_correct: false,
      alpha_cleanup: true,
      hard_alpha: true,
      despeckle: true,
    },
    smooth: {
      ...defaults,
      downscale_filter: "lanczos",
      preblur: 0.35,
      sharpen: 1.1,
      quantize: false,
      colour_count: 64,
      alpha_aware: true,
      gamma_correct: true,
      alpha_cleanup: true,
      hard_alpha: false,
      despeckle: false,
    },
  };
}

function qualitySettings(view) {
  return {
    downscale_filter: view.card.querySelector(".quality-filter").value,
    preblur: Number(view.card.querySelector(".quality-preblur").value),
    sharpen: Number(view.card.querySelector(".quality-sharpen").value),
    quantize: view.card.querySelector(".quality-quantize").checked,
    colour_count: Number(view.card.querySelector(".quality-colours").value),
    alpha_aware: view.card.querySelector(".quality-alpha-aware").checked,
    gamma_correct: view.card.querySelector(".quality-gamma").checked,
    alpha_cleanup: view.card.querySelector(".quality-alpha-cleanup").checked,
    hard_alpha: view.card.querySelector(".quality-hard-alpha").checked,
    despeckle: view.card.querySelector(".quality-despeckle").checked,
  };
}

function updateQualityLabels(view) {
  const values = qualitySettings(view);
  view.card.querySelector(".quality-sharpen-value").textContent = `${values.sharpen.toFixed(2)}×`;
  view.card.querySelector(".quality-preblur-value").textContent = `${values.preblur.toFixed(2)} px`;
  view.card.querySelector(".quality-colours-value").textContent = String(values.colour_count);
  view.card.querySelector(".quality-colours").disabled = !values.quantize;
}

function sameQualitySettings(left, right) {
  return QUALITY_KEYS.every((key) => {
    if (typeof left[key] === "number" || typeof right[key] === "number") {
      return Math.abs(Number(left[key]) - Number(right[key])) < 0.001;
    }
    return left[key] === right[key];
  });
}

function setQualityControls(view, settings = null, preferredPreset = "") {
  const presets = qualityPresetValues();
  const values = { ...presets.balanced, ...(settings || {}) };
  view.card.querySelector(".quality-filter").value = values.downscale_filter || "auto";
  view.card.querySelector(".quality-preblur").value = Number(values.preblur ?? 0.15);
  view.card.querySelector(".quality-sharpen").value = Number(values.sharpen ?? 1.45);
  view.card.querySelector(".quality-quantize").checked = Boolean(values.quantize);
  view.card.querySelector(".quality-colours").value = Number(values.colour_count ?? 48);
  view.card.querySelector(".quality-alpha-aware").checked = Boolean(values.alpha_aware);
  view.card.querySelector(".quality-gamma").checked = Boolean(values.gamma_correct);
  view.card.querySelector(".quality-alpha-cleanup").checked = Boolean(values.alpha_cleanup);
  view.card.querySelector(".quality-hard-alpha").checked = Boolean(values.hard_alpha);
  view.card.querySelector(".quality-despeckle").checked = Boolean(values.despeckle);
  const matched = Object.entries(presets).find(([_name, preset]) => sameQualitySettings(values, preset));
  view.card.querySelector(".quality-preset").value = preferredPreset && presets[preferredPreset]
    ? preferredPreset
    : (matched ? matched[0] : "custom");
  updateQualityLabels(view);
}

function processingSettings(view) {
  const settings = { ...qualitySettings(view) };
  if (view.group.mode === "crop_pair") {
    Object.assign(settings, cropValues(view));
  } else if (view.group.mode !== "passthrough") {
    Object.assign(settings, detectionSettings(view));
  }
  return settings;
}

function wireQualityControls(view) {
  const card = view.card;
  const preset = card.querySelector(".quality-preset");
  preset.addEventListener("change", () => {
    if (preset.value === "custom") return;
    setQualityControls(view, qualityPresetValues()[preset.value], preset.value);
  });
  [
    ".quality-filter",
    ".quality-preblur",
    ".quality-sharpen",
    ".quality-quantize",
    ".quality-colours",
    ".quality-alpha-aware",
    ".quality-gamma",
    ".quality-alpha-cleanup",
    ".quality-hard-alpha",
    ".quality-despeckle",
  ].forEach((selector) => {
    card.querySelector(selector).addEventListener("input", () => {
      preset.value = "custom";
      updateQualityLabels(view);
    });
  });
  card.querySelector(".apply-quality").addEventListener("click", () => reprocessGroup(view));
  setQualityControls(view);
}

function cropValues(view) {
  return {
    zoom: Number(view.card.querySelector(".crop-zoom").value),
    pan_x: Number(view.card.querySelector(".crop-x").value),
    pan_y: Number(view.card.querySelector(".crop-y").value),
  };
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function updateCropLabels(view) {
  const values = cropValues(view);
  view.card.querySelector(".crop-zoom-value").textContent = `${Math.round(values.zoom * 100)}%`;
  view.card.querySelector(".crop-x-value").textContent = `${Math.round(values.pan_x * 100)}%`;
  view.card.querySelector(".crop-y-value").textContent = `${Math.round(values.pan_y * 100)}%`;
}

function drawCropPreview(view) {
  updateCropLabels(view);
  if (!view.cropImage || !view.cropImage.complete || !view.cropImage.naturalWidth) return;
  const canvas = view.card.querySelector(".crop-canvas");
  const context = canvas.getContext("2d");
  const values = cropValues(view);
  const fallback = [0, 0, view.cropImage.naturalWidth, view.cropImage.naturalHeight];
  const box = view.cropBox && view.cropBox.length === 4 ? view.cropBox : fallback;
  const sourceWidth = Math.max(1, box[2] - box[0]);
  const sourceHeight = Math.max(1, box[3] - box[1]);
  const scale = 0.9 * Math.min(canvas.width / sourceWidth, canvas.height / sourceHeight) * values.zoom;
  const drawWidth = sourceWidth * scale;
  const drawHeight = sourceHeight * scale;
  const x = (canvas.width - drawWidth) / 2 + values.pan_x * canvas.width / 2;
  const y = (canvas.height - drawHeight) / 2 + values.pan_y * canvas.height / 2;
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = "high";
  context.drawImage(
    view.cropImage,
    box[0], box[1], sourceWidth, sourceHeight,
    x, y, drawWidth, drawHeight,
  );
}

function setCropInputValues(view, crop = null) {
  crop = crop || { zoom: 1, pan_x: 0, pan_y: 0 };
  view.card.querySelector(".crop-zoom").value = crop.zoom ?? 1;
  view.card.querySelector(".crop-x").value = crop.pan_x ?? 0;
  view.card.querySelector(".crop-y").value = crop.pan_y ?? 0;
  updateCropLabels(view);
}

function setCropControls(view, saved) {
  setCropInputValues(view, saved.crop);
  view.cropBox = saved.crop_bbox || [0, 0, saved.source_width || 1, saved.source_height || 1];
  if (view.cropRawVersion !== saved.raw_version) {
    view.cropRawVersion = saved.raw_version;
    const image = new Image();
    view.cropImage = image;
    image.addEventListener("load", () => drawCropPreview(view));
    image.addEventListener("error", () => {
      view.card.querySelector(".crop-status").textContent = "Could not load the crop source preview.";
    });
    image.src = draftRawUrl(view.creator, view.group.id, saved.raw_version || "");
  }
  drawCropPreview(view);
}

function wireCropControls(view) {
  const card = view.card;
  [".crop-zoom", ".crop-x", ".crop-y"].forEach((selector) => {
    card.querySelector(selector).addEventListener("input", () => drawCropPreview(view));
  });
  card.querySelector(".crop-reset").addEventListener("click", () => {
    card.querySelector(".crop-zoom").value = 1;
    card.querySelector(".crop-x").value = 0;
    card.querySelector(".crop-y").value = 0;
    drawCropPreview(view);
  });
  card.querySelector(".apply-crop").addEventListener("click", () => reprocessGroup(view));

  const wrap = card.querySelector(".crop-canvas-wrap");
  const canvas = card.querySelector(".crop-canvas");
  canvas.addEventListener("pointerdown", (event) => {
    canvas.setPointerCapture(event.pointerId);
    wrap.classList.add("dragging");
    const values = cropValues(view);
    view.cropDrag = { x: event.clientX, y: event.clientY, panX: values.pan_x, panY: values.pan_y };
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!view.cropDrag) return;
    const bounds = canvas.getBoundingClientRect();
    const panX = clamp(view.cropDrag.panX + ((event.clientX - view.cropDrag.x) / bounds.width) * 2, -2, 2);
    const panY = clamp(view.cropDrag.panY + ((event.clientY - view.cropDrag.y) / bounds.height) * 2, -2, 2);
    card.querySelector(".crop-x").value = panX;
    card.querySelector(".crop-y").value = panY;
    drawCropPreview(view);
  });
  const endDrag = () => {
    view.cropDrag = null;
    wrap.classList.remove("dragging");
  };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const input = card.querySelector(".crop-zoom");
    input.value = clamp(Number(input.value) * Math.exp(-event.deltaY * 0.0015), 0.1, 6);
    drawCropPreview(view);
  }, { passive: false });
}

async function reprocessGroup(view, processing = null) {
  const button = view.group.mode === "crop_pair"
    ? view.card.querySelector(".apply-crop")
    : view.card.querySelector(".reprocess-group");
  const relatedButtons = [
    view.card.querySelector(".apply-crop"),
    view.card.querySelector(".reprocess-group"),
    view.card.querySelector(".apply-quality"),
  ].filter(Boolean);
  relatedButtons.forEach((item) => { item.disabled = true; });
  const oldText = button.textContent;
  button.textContent = "Processing…";
  try {
    const result = await api("/api/reprocess", {
      method: "POST",
      json: {
        draft: view.creator.draftId,
        profile: view.creator.profileKey,
        group: view.group.id,
        processing: processing || processingSettings(view),
      },
    });
    view.creator.draftStatus = result;
    renderCreatorDraft(view.creator);
    toast(`${view.group.label} reprocessed.`);
  } catch (error) {
    toast(error.message, "error");
    await loadCreatorDraft(view.creator, false);
  } finally {
    relatedButtons.forEach((item) => { item.disabled = false; });
    button.textContent = oldText;
  }
}

function collectProcessingPresets(creator) {
  const presets = {};
  creator.cards.forEach((view, groupId) => {
    if (view.group.mode === "passthrough") return;
    const entry = {
      preset: view.card.querySelector(".quality-preset").value,
      quality: qualitySettings(view),
    };
    if (view.group.mode === "crop_pair") {
      entry.crop = cropValues(view);
    } else {
      entry.detection = detectionSettings(view);
    }
    presets[groupId] = entry;
  });
  return presets;
}

function saveDetailsFile(creator) {
  const details = {
    version: 2,
    profile: creator.profileKey,
    name: creator.nameInput.value.trim(),
    slug: creator.slugInput.value.trim(),
    description: creator.descriptionInput.value.trim(),
    processing_presets: collectProcessingPresets(creator),
  };
  const blob = new Blob([`${JSON.stringify(details, null, 2)}\n`], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${details.slug || creator.profileKey}-sprite-details.json`;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
  persistIdentity(creator);
  toast("Sprite details save file created.");
}

async function loadDetailsFile(creator, file) {
  try {
    const details = JSON.parse(await file.text());
    if (!details || typeof details !== "object") throw new Error("The save file does not contain an object.");
    if (details.profile && details.profile !== creator.profileKey) {
      throw new Error(`This is a ${details.profile} save, not a ${creator.profileKey} save.`);
    }
    creator.nameInput.value = typeof details.name === "string" ? details.name : "";
    creator.slugInput.value = slugify(typeof details.slug === "string" ? details.slug : creator.nameInput.value);
    creator.descriptionInput.value = typeof details.description === "string" ? details.description : "";
    creator.slugTouched = true;
    saveCreatorForm(creator);
    await persistIdentity(creator);
    creator.cards.forEach(({ group, promptArea }) => {
      promptArea.value = buildPrompt(creator.profile, group, creator.nameInput.value, creator.descriptionInput.value);
    });
    renderCreatorDraft(creator);
    const presets = details.processing_presets && typeof details.processing_presets === "object"
      ? details.processing_presets
      : {};
    creator.cards.forEach((view, groupId) => {
      const saved = presets[groupId];
      if (!saved || typeof saved !== "object") return;
      if (saved.quality && typeof saved.quality === "object") {
        setQualityControls(view, saved.quality, typeof saved.preset === "string" ? saved.preset : "");
      }
      if (view.group.mode === "crop_pair" && saved.crop && typeof saved.crop === "object") {
        setCropInputValues(view, saved.crop);
        drawCropPreview(view);
      } else if (view.group.mode !== "passthrough" && saved.detection && typeof saved.detection === "object") {
        setDetectionControls(view, saved.detection);
      }
    });
    toast(Object.keys(presets).length
      ? "Sprite details and 32×32 processing presets loaded."
      : "Sprite details loaded.");
  } catch (error) {
    toast(`Could not load details: ${error.message}`, "error");
  }
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
    identitySaveTimer: null,
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
    const isCrop = group.mode === "crop_pair";
    input.accept = isCrop ? "image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp" : "image/png,.png";
    card.querySelector(".drop-icon").textContent = isCrop ? "IMAGE" : "PNG";
    card.querySelector(".drop-title").textContent = isCrop ? "Choose source artwork" : "Choose generated spritesheet";
    card.querySelector(".drop-help").textContent = isCrop
      ? "PNG, JPEG or WebP at any size. The crop tool creates both final still images."
      : "Automatic detection runs immediately. Upload again to replace this group.";
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      if (file) uploadGroup(creator, group, file);
      input.value = "";
    });
    const view = { card, group, promptArea, input, creator, cropImage: null, cropRawVersion: null };
    creator.cards.set(group.id, view);
    if (group.mode !== "passthrough") {
      card.querySelector(".quality-controls").classList.remove("hidden");
      wireQualityControls(view);
    }
    if (isCrop) {
      wireCropControls(view);
    } else if (group.mode !== "passthrough") {
      card.querySelector(".detection-controls").classList.remove("hidden");
      setDetectionControls(view);
      card.querySelector(".reprocess-group").addEventListener("click", () => reprocessGroup(view));
    }
    grid.append(cardFragment);
  });

  creator.nameInput.addEventListener("input", () => {
    if (!creator.slugTouched) creator.slugInput.value = slugify(creator.nameInput.value);
    saveCreatorForm(creator);
    scheduleIdentitySave(creator);
    updateCreatorProgress(creator);
    renderCreatorDraft(creator);
  });
  creator.slugInput.addEventListener("input", () => {
    creator.slugInput.value = slugify(creator.slugInput.value);
    creator.slugTouched = true;
    saveCreatorForm(creator);
    scheduleIdentitySave(creator);
    updateCreatorProgress(creator);
    renderCreatorDraft(creator);
  });
  creator.descriptionInput.addEventListener("input", () => {
    saveCreatorForm(creator);
    scheduleIdentitySave(creator);
    updateCreatorProgress(creator);
  });
  page.querySelector(".generate-prompts").addEventListener("click", () => generateCreatorPrompts(creator));
  page.querySelector(".reset-draft").addEventListener("click", () => resetCreatorDraft(creator));
  page.querySelector(".export-button").addEventListener("click", () => exportCreator(creator));
  page.querySelector(".save-details").addEventListener("click", () => saveDetailsFile(creator));
  const loadInput = page.querySelector(".load-details-input");
  page.querySelector(".load-details").addEventListener("click", () => loadInput.click());
  loadInput.addEventListener("change", () => {
    const file = loadInput.files && loadInput.files[0];
    if (file) loadDetailsFile(creator, file);
    loadInput.value = "";
  });
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
  scheduleIdentitySave(creator);
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

async function loadCreatorDraft(creator, notify = true) {
  try {
    const result = await api(buildUrl("/api/draft", { draft: creator.draftId, profile: creator.profileKey }));
    creator.draftStatus = result;
    const identity = result.identity || {};
    if (identity.name || identity.slug || identity.description) {
      creator.nameInput.value = identity.name || "";
      creator.slugInput.value = identity.slug || "";
      creator.descriptionInput.value = identity.description || "";
      creator.slugTouched = Boolean(identity.slug);
      saveCreatorForm(creator);
      creator.cards.forEach(({ group, promptArea }) => {
        promptArea.value = buildPrompt(
          creator.profile,
          group,
          creator.nameInput.value,
          creator.descriptionInput.value,
        );
      });
    }
    renderCreatorDraft(creator);
  } catch (error) {
    if (notify) toast(`Could not load ${creator.profile.label.toLowerCase()} draft: ${error.message}`, "error");
  }
}

async function uploadGroup(creator, group, file) {
  const view = creator.cards.get(group.id);
  const cropMode = group.mode === "crop_pair";
  if ((!cropMode && !/\.png$/i.test(file.name)) || (cropMode && !/\.(png|jpe?g|webp)$/i.test(file.name))) {
    toast(cropMode ? "Choose a PNG, JPEG or WebP image." : "Choose a PNG file.", "error");
    return;
  }
  view.input.disabled = true;
  view.card.classList.remove("ready", "error");
  view.card.querySelector(".completion-badge").textContent = "Processing";
  const message = view.card.querySelector(".upload-message");
  message.className = "upload-message";
  message.textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MB · ${cropMode ? "preparing crop…" : "detecting sprites…"}`;
  try {
    const params = {
      draft: creator.draftId,
      profile: creator.profileKey,
      group: group.id,
      ...(group.mode === "passthrough" ? {} : processingSettings(view)),
    };
    const result = await api(buildUrl("/api/upload", params), {
      method: "POST",
      headers: { "Content-Type": file.type || "application/octet-stream" },
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
    await loadCreatorDraft(creator, false);
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
    if (view.group.mode === "crop_pair") {
      const cropEditor = view.card.querySelector(".crop-editor");
      cropEditor.classList.toggle("hidden", !saved || !saved.raw_name);
      if (saved && saved.raw_name) {
        const cropSignature = `${saved.raw_version || ""}:${JSON.stringify(saved.crop || {})}`;
        if (view.cropSignature !== cropSignature) {
          view.cropSignature = cropSignature;
          setCropControls(view, saved);
        } else {
          drawCropPreview(view);
        }
        view.card.querySelector(".crop-status").textContent = saved.ready
          ? "Crop applied. Both the 32x32 sprite and 256x256 examine asset are shown below."
          : "Adjust the crop and choose Apply crop to try again.";
      }
    } else if (view.group.mode !== "passthrough") {
      const reprocess = view.card.querySelector(".reprocess-group");
      reprocess.disabled = !saved || !saved.raw_name;
      if (saved && saved.settings) {
        const settingsSignature = JSON.stringify(saved.settings);
        if (view.settingsSignature !== settingsSignature) {
          view.settingsSignature = settingsSignature;
          setDetectionControls(view, saved.settings);
        }
      }
      view.card.querySelector(".detection-method").textContent = saved && saved.processing_method
        ? `Last result: ${saved.processing_method}`
        : "No image processed yet.";
    }
    if (view.group.mode !== "passthrough") {
      const qualityButton = view.card.querySelector(".apply-quality");
      qualityButton.disabled = !saved || !saved.raw_name;
      if (saved && saved.quality) {
        const qualitySignature = JSON.stringify(saved.quality);
        if (view.qualitySignature !== qualitySignature) {
          view.qualitySignature = qualitySignature;
          setQualityControls(view, saved.quality);
        }
        view.card.querySelector(".quality-note").textContent =
          `Last output: ${saved.quality.downscale_filter} filter, ${Number(saved.quality.sharpen).toFixed(2)}× sharpness, `
          + `${saved.quality.quantize ? `${saved.quality.colour_count}-colour palette` : "full colour"}.`;
      } else {
        view.card.querySelector(".quality-note").textContent =
          "Settings are kept with the draft and included in creature detail save files.";
      }
    }
    if (saved && saved.ready) {
      view.card.classList.add("ready");
      badge.textContent = "Ready";
      message.className = "upload-message";
      const warningText = (saved.warnings || []).join(" ");
      const method = saved.processing_method ? ` · ${saved.processing_method}` : "";
      message.textContent = `Source ${saved.source_width}x${saved.source_height} processed successfully${method}.${warningText ? ` ${warningText}` : ""}`;
      saved.outputs.forEach((record) => {
        const item = document.createElement("div");
        item.className = "processed-item";
        const preview = document.createElement("div");
        preview.className = "asset-preview";
        const finalName = `${creator.slugInput.value || "name"}${record.suffix}`;
        addSpritePreviewComparison(
          preview,
          { ...record, name: finalName, frame_width: record.width / record.frames, frame_height: record.height },
          draftMediaUrl(creator.draftId, record.stored_name),
        );
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
  persistIdentity(creator);
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
      json: {
        draft: creator.draftId,
        profile: creator.profileKey,
        slug: creator.slugInput.value.trim(),
        name: creator.nameInput.value.trim(),
        description: creator.descriptionInput.value.trim(),
      },
    });
    resultBox.classList.remove("hidden");
    resultBox.textContent = `Created ${result.relative_path} with ${result.file_count} game PNGs and ${result.source_relative_path} with ${result.source_file_count} full-size source PNGs. ${result.note}`;
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
    const haystack = [entity.name, entity.display_name, entity.description, entity.relative_path]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    const searchMatch = !search || haystack.includes(search);
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
    name.textContent = entity.display_name || entity.name;
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = String(entity.asset_count);
    const type = document.createElement("span");
    type.className = "type";
    type.textContent = entity.display_name && entity.display_name !== entity.name
      ? `${entity.profile_label} · ${entity.name}`
      : entity.profile_label;
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
  heading.textContent = entity.display_name || entity.name;
  const path = document.createElement("p");
  path.textContent = `${entity.relative_path} · ${entity.asset_count} PNG${entity.asset_count === 1 ? "" : "s"}`;
  header.append(heading, path);
  detail.append(header);
  const metadata = document.createElement("div");
  metadata.className = "library-metadata";
  const nameLabel = document.createElement("label");
  const nameTitle = document.createElement("span");
  nameTitle.textContent = "Name";
  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.maxLength = 120;
  nameInput.value = entity.display_name || entity.name;
  nameInput.placeholder = entity.name;
  nameLabel.append(nameTitle, nameInput);
  const descriptionLabel = document.createElement("label");
  const descriptionTitle = document.createElement("span");
  descriptionTitle.textContent = "Description";
  const descriptionInput = document.createElement("textarea");
  descriptionInput.rows = 3;
  descriptionInput.maxLength = 5000;
  descriptionInput.value = entity.description || "";
  descriptionInput.placeholder = "No description saved yet — add one here for future prompt generation.";
  descriptionLabel.append(descriptionTitle, descriptionInput);
  const save = document.createElement("button");
  save.className = "button primary";
  save.textContent = "Save details";
  const note = document.createElement("p");
  note.className = "metadata-note";
  note.textContent = entity.metadata_present
    ? "Saved with the Sprite Library and editable at any time."
    : "This folder has no saved description yet. Add one now and it will appear on future library scans.";
  save.addEventListener("click", async () => {
    save.disabled = true;
    save.textContent = "Saving…";
    try {
      await api("/api/library/metadata", {
        method: "POST",
        json: {
          profile: entity.profile,
          folder: entity.folder || entity.name,
          name: nameInput.value,
          description: descriptionInput.value,
        },
      });
      entity.display_name = nameInput.value.trim() || entity.name;
      entity.description = descriptionInput.value.trim();
      entity.metadata_present = Boolean(nameInput.value.trim() || descriptionInput.value.trim());
      toast(`Details saved for ${entity.display_name}.`);
      renderEntityList();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      save.disabled = false;
      save.textContent = "Save details";
    }
  });
  metadata.append(nameLabel, descriptionLabel, save, note);
  detail.append(metadata);
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
      addSpritePreviewComparison(preview, asset, mediaUrl(asset.relative_path));
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
