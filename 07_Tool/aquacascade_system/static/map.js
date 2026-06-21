(function () {
  const MATERIAL = {
    "Lead": "#B23B3B",
    "Galvanized Requiring Replacement": "#C77816",
    "Lead Status Unknown": "#5F6F84",
    "Non-lead": "#1F7A4D"
  };
  const STATUS = {
    "OPEN": "#526176",
    "ASSIGNED": "#0A6E9E",
    "IN_PROGRESS": "#9A6700",
    "DONE": "#1F7A4D",
    "CANCELLED": "#9CA7B7"
  };

  const app = document.getElementById("map-app");
  if (!app) return;
  const detail = document.getElementById("line-detail");
  const bars = document.getElementById("composition-bars");
  const legend = document.getElementById("map-legend");
  const filter = document.getElementById("map-filter");
  const note = document.getElementById("map-note");
  const refresh = document.getElementById("map-refresh");
  const layerLabel = document.getElementById("map-layer-label");
  const mode = document.getElementById("map-mode");
  const stateFocus = app.dataset.state || "";
  let data = null;
  let selectedId = null;
  let map = null;
  let lineLayer = null;
  let inventoryRenderer = null;
  let detailRenderer = null;
  let hasFitBounds = false;
  let lastFitFilter = null;
  let mapStats = { mode: "loading", visible: 0, rendered: 0, clusters: 0 };
  const NATIONAL_US_BOUNDS = [[24.4, -125.0], [49.6, -66.5]];

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[ch]));
  }

  function materialColor(material) {
    return MATERIAL[material] || "#0A6E9E";
  }

  function statusColor(status) {
    return STATUS[status] || "#FFFFFF";
  }

  function setText(id, value) {
    document.getElementById(id).textContent = value;
  }

  function formatTime(iso) {
    if (!iso) return "--";
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function fmt(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n.toLocaleString() : String(value || "0");
  }

  function fmtShort(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return String(value || "0");
    if (Math.abs(n) >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
    if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(1)}k`;
    return String(Math.round(n));
  }

  function initMap() {
    if (map) return true;
    if (typeof L === "undefined") {
      note.textContent = "OpenStreetMap library did not load. Check network access.";
      return false;
    }
    map = L.map("service-map", {
      zoomControl: true,
      minZoom: 3,
      preferCanvas: true
    }).setView([39.5, -98.35], 4);
    inventoryRenderer = L.canvas({ padding: 0.5 });
    detailRenderer = L.svg({ padding: 0.5 });
    L.tileLayer(app.dataset.tileUrl, {
      maxZoom: 19,
      attribution: app.dataset.tileAttribution
    }).addTo(map);
    lineLayer = L.layerGroup().addTo(map);
    map.on("zoomend moveend", () => {
      if (!data) return;
      renderMapLayer();
      updateMapStatus();
    });
    window.setTimeout(() => map.invalidateSize(), 50);
    return true;
  }

  async function load() {
    refresh.disabled = true;
    note.textContent = "Refreshing...";
    try {
      const res = await fetch(app.dataset.url, {
        headers: { "Accept": "application/json" }
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
      render();
      updateMapStatus();
    } catch (err) {
      note.textContent = `Map data unavailable: ${err.message}`;
      if (mode) mode.textContent = "Map data unavailable.";
    } finally {
      refresh.disabled = false;
    }
  }

  function render() {
    setText("map-total", fmt(data.counts.total));
    setText("map-mapped", fmt(data.counts.mapped));
    setText("map-unmapped", fmt(data.counts.unmapped));
    setText("map-total-label", data.labels?.total || "Service lines");
    setText("map-mapped-label", data.labels?.mapped || "Mapped");
    setText("map-unmapped-label", data.labels?.unmapped || "Unmapped");
    setText("map-updated", formatTime(data.generated_at));
    if (layerLabel) {
      const layerName = data.layer === "system_inventory" ?
        "Live SDWIS inventory layer" : "Live service-line layer";
      layerLabel.textContent = data.state_filter ?
        `${layerName} - ${data.state_filter}` : layerName;
    }
    renderLegend();
    renderFilter();
    renderBars();
    renderMapLayer();
    const selected = data.features.find((f) => String(f.id) === String(selectedId));
    renderDetail(selected || null);
  }

  function renderLegend() {
    legend.innerHTML = "";
    Object.entries(MATERIAL).forEach(([label, color]) => {
      const item = document.createElement("span");
      item.className = "legend-item";
      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.background = color;
      item.appendChild(swatch);
      item.appendChild(document.createTextNode(label));
      legend.appendChild(item);
    });
  }

  function renderFilter() {
    const current = filter.value;
    const materials = data.composition.map((x) => x.material);
    filter.innerHTML = "";
    const all = document.createElement("option");
    all.value = "";
    all.textContent = "All materials";
    filter.appendChild(all);
    materials.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      filter.appendChild(opt);
    });
    filter.value = materials.includes(current) ? current : "";
  }

  function renderBars() {
    bars.innerHTML = "";
    if (!data.composition.length) {
      bars.innerHTML = '<div class="empty">No service lines.</div>';
      return;
    }
    data.composition.forEach((row) => {
      const wrap = document.createElement("div");
      wrap.className = "comp-row";
      const top = document.createElement("div");
      top.className = "comp-top";
      top.innerHTML = "<b></b><span></span>";
      top.querySelector("b").textContent = row.material;
      top.querySelector("span").textContent =
        `${fmt(row.count)} (${row.pct.toFixed(1)}%)`;
      const bar = document.createElement("div");
      bar.className = "comp-track";
      const fill = document.createElement("div");
      fill.className = "comp-fill";
      fill.style.width = `${Math.max(row.pct, row.count ? 3 : 0)}%`;
      fill.style.background = materialColor(row.material);
      bar.appendChild(fill);
      wrap.appendChild(top);
      wrap.appendChild(bar);
      bars.appendChild(wrap);
    });
  }

  function renderMapLayer() {
    if (!initMap()) return;
    const mapped = data.features.filter((f) => f.geometry &&
      (!filter.value || f.material === filter.value));
    const bounds = [];
    mapped.forEach((f) => collectFeatureBounds(f, bounds));
    if (!mapped.length) {
      lineLayer.clearLayers();
      mapStats = { mode: "empty", visible: 0, rendered: 0, clusters: 0 };
      note.textContent = "No mapped service lines for this filter.";
      return;
    }
    const currentFilter = filter.value || "";
    if (!hasFitBounds || currentFilter !== lastFitFilter) {
      hasFitBounds = true;
      lastFitFilter = currentFilter;
      fitMapBounds(bounds);
    }

    lineLayer.clearLayers();
    if (shouldCluster()) {
      renderClusters(mapped);
      return;
    }

    const visible = visibleFeatures(mapped);
    visible.forEach((f) => {
      const layers = layersForFeature(f);
      layers.forEach((layer) => {
        layer.on("click", () => selectFeature(f.id));
        const tooltip = f.feature_kind === "system_inventory" ?
          `${f.system_name || f.service_line_id} - ${f.material}` :
          `${f.service_line_id} - ${f.material}`;
        layer.bindTooltip(tooltip, {
          sticky: true
        });
        lineLayer.addLayer(layer);
      });
    });
    mapStats = {
      mode: "detail",
      visible: visible.length,
      rendered: visible.length,
      clusters: 0
    };
  }

  function shouldCluster() {
    return data.layer === "system_inventory" && map.getZoom() < 8;
  }

  function visibleFeatures(features) {
    const b = map.getBounds().pad(0.18);
    return features.filter((f) => {
      const ll = featureLatLng(f);
      return ll && b.contains(ll);
    });
  }

  function renderClusters(features) {
    const clusters = buildClusters(features);
    clusters.forEach((cluster) => {
      const layer = clusterLayer(cluster);
      lineLayer.addLayer(layer);
    });
    mapStats = {
      mode: "cluster",
      visible: clusters.reduce((acc, c) => acc + c.features, 0),
      rendered: clusters.length,
      clusters: clusters.length
    };
  }

  function buildClusters(features) {
    const zoom = map.getZoom();
    const cell = zoom <= 4 ? 82 : (zoom <= 5 ? 70 : 58);
    const bounds = map.getBounds().pad(0.16);
    const groups = new Map();
    features.forEach((f) => {
      const ll = featureLatLng(f);
      if (!ll || !bounds.contains(ll)) return;
      const point = map.project(ll, zoom);
      const key = `${Math.floor(point.x / cell)}:${Math.floor(point.y / cell)}`;
      let g = groups.get(key);
      if (!g) {
        g = {
          features: 0,
          latSum: 0,
          lngSum: 0,
          total: 0,
          lead: 0,
          grr: 0,
          unknown: 0,
          nonlead: 0,
          bounds: L.latLngBounds([])
        };
        groups.set(key, g);
      }
      const c = featureMaterialCounts(f);
      g.features += 1;
      g.latSum += ll.lat;
      g.lngSum += ll.lng;
      g.total += c.total;
      g.lead += c.lead;
      g.grr += c.grr;
      g.unknown += c.unknown;
      g.nonlead += c.nonlead;
      g.bounds.extend(ll);
    });
    return Array.from(groups.values()).map((g) => {
      const counts = [
        ["Lead", g.lead],
        ["Galvanized Requiring Replacement", g.grr],
        ["Lead Status Unknown", g.unknown],
        ["Non-lead", g.nonlead]
      ];
      const dominant = counts.reduce((best, row) =>
        row[1] > best[1] ? row : best, counts[0])[0];
      return {
        ...g,
        lat: g.latSum / g.features,
        lng: g.lngSum / g.features,
        material: dominant
      };
    });
  }

  function clusterLayer(cluster) {
    const color = materialColor(cluster.material);
    const size = Math.max(44, Math.min(72,
      36 + Math.log10(Math.max(10, cluster.features)) * 13));
    const html = `<div class="map-cluster-bubble" style="width:${size}px;` +
      `height:${size}px;background:${color}"><b>${fmtShort(cluster.features)}</b>` +
      `<span>systems</span></div>`;
    const layer = L.marker([cluster.lat, cluster.lng], {
      icon: L.divIcon({
        className: "map-cluster-icon",
        html,
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2]
      })
    });
    layer.bindTooltip(`${fmt(cluster.features)} systems, ` +
      `${fmt(cluster.total)} reported lines, ` +
      `dominant material: ${cluster.material}`, { sticky: true });
    layer.on("click", () => {
      if (cluster.bounds.isValid()) {
        const targetZoom = Math.max(8, map.getZoom() + 3);
        const center = cluster.bounds.getCenter();
        map.setView(center, targetZoom);
      }
    });
    return layer;
  }

  function featureLatLng(f) {
    if (Number.isFinite(Number(f.latitude)) &&
        Number.isFinite(Number(f.longitude))) {
      return L.latLng(Number(f.latitude), Number(f.longitude));
    }
    if (f.geometry && f.geometry.type === "Point") {
      const p = toLatLng(f.geometry.coordinates);
      return p ? L.latLng(p[0], p[1]) : null;
    }
    return null;
  }

  function featureMaterialCounts(f) {
    const lead = Number(f.lead_count || 0);
    const grr = Number(f.grr_count || 0);
    const unknown = Number(f.unknown_count || 0);
    const nonlead = Number(f.nonlead_count || 0);
    let total = Number(f.total_count || 0);
    if (!total) {
      total = 1;
      return {
        lead: f.material === "Lead" ? 1 : 0,
        grr: f.material === "Galvanized Requiring Replacement" ? 1 : 0,
        unknown: f.material === "Lead Status Unknown" ? 1 : 0,
        nonlead: f.material === "Non-lead" ? 1 : 0,
        total
      };
    }
    return { lead, grr, unknown, nonlead, total };
  }

  function fitMapBounds(bounds) {
    const b = L.latLngBounds(bounds);
    if (!b.isValid()) return;
    const wideNationalLayer = data.layer === "system_inventory" &&
      !filter.value && !data.state_filter && (Math.abs(b.getEast() - b.getWest()) > 90 ||
      Math.abs(b.getNorth() - b.getSouth()) > 45);
    if (wideNationalLayer) {
      map.fitBounds(NATIONAL_US_BOUNDS, { padding: [18, 18], maxZoom: 5 });
      return;
    }
    map.fitBounds(b.pad(0.25), { maxZoom: 16 });
  }

  function layersForFeature(f) {
    const selected = String(f.id) === String(selectedId);
    const geom = f.geometry;
    const color = materialColor(f.material);
    const stroke = selected ? "#172339" : statusColor(f.work_order_status);
    const lineStyle = {
      color,
      weight: selected ? 9 : (f.work_order_status === "IN_PROGRESS" ? 7 : 5),
      opacity: 0.9,
      lineCap: "round",
      lineJoin: "round",
      pane: "overlayPane"
    };
    const haloStyle = {
      color: stroke,
      weight: selected ? 13 : 9,
      opacity: selected ? 0.75 : 0.32,
      lineCap: "round",
      lineJoin: "round"
    };
    if (geom.type === "Point") {
      const p = toLatLng(geom.coordinates);
      if (!p) return [];
      const baseRadius = f.feature_kind === "system_inventory" ?
        Math.max(5, Math.min(20, 4 + Math.log10(
          Math.max(1, Number(f.total_count || 1))) * 4)) : 7;
      return [L.circleMarker(p, {
        radius: selected ? baseRadius + 2 : baseRadius,
        color: stroke,
        weight: selected ? 3 : 2,
        fillColor: color,
        fillOpacity: 0.88,
        renderer: f.feature_kind === "system_inventory" ?
          (shouldCluster() ? inventoryRenderer : detailRenderer) : undefined
      })];
    }
    const lines = geom.type === "LineString" ? [geom.coordinates] :
      (geom.type === "MultiLineString" ? geom.coordinates : []);
    const layers = [];
    lines.forEach((line) => {
      const latlngs = line.map(toLatLng).filter(Boolean);
      if (latlngs.length < 2) return;
      layers.push(L.polyline(latlngs, haloStyle));
      layers.push(L.polyline(latlngs, lineStyle));
    });
    if (Number.isFinite(Number(f.latitude)) &&
        Number.isFinite(Number(f.longitude))) {
      layers.push(L.circleMarker([Number(f.latitude), Number(f.longitude)], {
        radius: selected ? 7 : 5,
        color: selected ? "#172339" : "#FFFFFF",
        weight: selected ? 3 : 1.5,
        fillColor: color,
        fillOpacity: 0.92,
        className: "line-map-node"
      }));
    }
    return layers;
  }

  function toLatLng(pair) {
    if (!Array.isArray(pair) || pair.length < 2) return null;
    const lon = Number(pair[0]);
    const lat = Number(pair[1]);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    return [lat, lon];
  }

  function collectBounds(layer, bounds) {
    if (layer.getLatLng) {
      bounds.push(layer.getLatLng());
    } else if (layer.getLatLngs) {
      layer.getLatLngs().forEach((p) => bounds.push(p));
    }
  }

  function collectFeatureBounds(f, bounds) {
    const ll = featureLatLng(f);
    if (ll) bounds.push(ll);
    if (!f.geometry || f.geometry.type === "Point") return;
    const lines = f.geometry.type === "LineString" ? [f.geometry.coordinates] :
      (f.geometry.type === "MultiLineString" ? f.geometry.coordinates : []);
    lines.forEach((line) => {
      line.map(toLatLng).filter(Boolean).forEach((p) => bounds.push(p));
    });
  }

  function updateMapStatus() {
    const systems = data.counts.systems ?
      ` across ${fmt(data.counts.systems)} systems` : "";
    if (mapStats.mode === "cluster") {
      note.textContent = `${fmt(mapStats.visible)} visible systems grouped ` +
        `into ${fmt(mapStats.clusters)} live clusters. Circle size shows ` +
        `how many systems are grouped; color shows dominant material. Click a cluster or ` +
        `zoom in for individual system metadata.`;
      if (mode) {
        const focus = data.state_filter || stateFocus;
        mode.innerHTML = `<b>Clustered national overview</b><span>` +
          `${focus ? `${focus} focus. ` : ""}${fmt(mapStats.visible)} visible systems, ${fmt(mapStats.clusters)} ` +
          `clusters. Size = grouped systems; color = dominant material. ` +
          `${fmt(data.counts.unmapped)} without usable geometry.</span>`;
      }
      return;
    }
    if (mapStats.mode === "detail") {
      note.textContent = `${fmt(mapStats.visible)} individual features visible` +
        `${systems}, ${fmt(data.counts.unmapped)} without usable geometry.`;
      if (mode) {
        mode.innerHTML = `<b>Individual system detail</b><span>` +
          `Click any visible system to inspect material counts and metadata.</span>`;
      }
      return;
    }
    if (mode) mode.textContent = "No mapped features for the current filter.";
  }

  function selectFeature(id) {
    selectedId = id;
    const f = data.features.find((x) => String(x.id) === String(id));
    renderMapLayer();
    renderDetail(f || null);
  }

  function renderDetail(f) {
    detail.innerHTML = "";
    if (!f) {
      detail.innerHTML = '<div class="empty">No feature selected.</div>';
      return;
    }
    const title = document.createElement("div");
    title.className = "detail-title";
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.style.background = materialColor(f.material);
    badge.style.color = "#fff";
    badge.textContent = f.material;
    const h = document.createElement("h3");
    h.textContent = f.service_line_id;
    title.appendChild(h);
    title.appendChild(badge);
    detail.appendChild(title);
    const coords = f.latitude === null || f.longitude === null ? "-" :
      `${Number(f.latitude).toFixed(5)}, ${Number(f.longitude).toFixed(5)}`;
    let rows;
    if (f.feature_kind === "system_inventory") {
      rows = [
        ["PWSID", f.pwsid],
        ["System", f.system_name || "-"],
        ["State", f.state || "-"],
        ["Quarter", f.source_quarter || "-"],
        ["Install year", "Not available in public SDWIS inventory"],
        ["Asset age", "Requires utility asset data"],
        ["Remaining service life", "Requires utility asset data"],
        ["Rehab use", "Use counts for screening; import asset records for age"],
        ["Dominant material", f.material || "-"],
        ["Lead lines", fmt(f.lead_count)],
        ["Galvanized requiring replacement", fmt(f.grr_count)],
        ["Lead status unknown", fmt(f.unknown_count)],
        ["Non-lead", fmt(f.nonlead_count)],
        ["Total reported", fmt(f.total_count)],
        ["Report status", f.report_status || "-"],
        ["PWS type", f.pws_type || "-"],
        ["Activity", f.activity_status || "-"],
        ["Population served", fmt(f.population_served)],
        ["Representative point", coords],
        ["Source", f.inventory_source || "-"]
      ];
    } else {
      rows = [
        ["PWSID", f.pwsid],
        ["System", f.system_name || "-"],
        ["State", f.state || "-"],
        ["Location", f.location || "-"],
        ["Geometry", f.geometry ? f.geometry.type : "-"],
        ["Representative point", coords],
        ["Install year", f.install_year || "-"],
        ["Expected service life", f.expected_service_life_years ?
          `${fmt(f.expected_service_life_years)} yrs (${f.service_life_basis || "Default assumption"})` : "-"],
        ["Asset age", f.asset_age_years === null ? "-" :
          `${fmt(f.asset_age_years)} yrs`],
        ["Remaining service life", f.remaining_life_years === null ? "-" :
          `${fmt(f.remaining_life_years)} yrs`],
        ["Renewal due year", f.renewal_due_year || "-"],
        ["Lifecycle flag", f.lifecycle_flag || "-"],
        ["Replacement year", f.replacement_year || "-"],
        ["Diameter", f.diameter_in ? `${f.diameter_in} in` : "-"],
        ["Length", f.length_ft ? `${f.length_ft} ft` : "-"],
        ["Ownership side", f.ownership_side || "-"],
        ["Verification method", f.verification_method || "-"],
        ["Evidence source", f.evidence_source || "-"],
        ["Confidence", f.confidence_score || "-"],
        ["Current status", f.current_status || "-"],
        ["System side", f.system_side_material || "-"],
        ["Customer side", f.customer_side_material || "-"],
        ["Investigation date", f.investigation_date || "-"],
        ["Work order", f.work_order_id ?
          `#${f.work_order_id} ${f.work_order_type || ""}` : "-"],
        ["Work status", f.work_order_status || "-"],
        ["Assigned to", f.assigned_to || "-"],
        ["Model rank", f.model_rank || "-"],
        ["Lead-rich probability", f.p_lead_rich === null ?
          "-" : Number(f.p_lead_rich).toFixed(3)],
        ["Basis", f.basis_of_classification || "-"],
        ["Notes", f.notes || "-"]
      ];
    }
    const table = document.createElement("table");
    table.className = "detail-table";
    rows.forEach(([k, v]) => {
      const tr = document.createElement("tr");
      const th = document.createElement("th");
      const td = document.createElement("td");
      th.textContent = k;
      td.textContent = v;
      tr.appendChild(th);
      tr.appendChild(td);
      table.appendChild(tr);
    });
    detail.appendChild(table);
  }

  refresh.addEventListener("click", () => {
    hasFitBounds = false;
    lastFitFilter = null;
    load();
  });
  filter.addEventListener("change", () => {
    selectedId = null;
    render();
  });

  if (initMap()) {
    load();
    window.setInterval(load, 30000);
  }
})();
