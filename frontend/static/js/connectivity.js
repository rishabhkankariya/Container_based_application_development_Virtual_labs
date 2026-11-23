/**
 * V-Lab Cluster Connectivity & Cloud Sync Manager  v11.0
 * Monitors /api/health with exponential backoff & jitter.
 * Handles auto-failover (Host 1 → Host 2) and auto-failback (Host 2 → Host 1).
 *
 * FAILBACK MECHANISM:
 *   localStorage is per-origin, so Host 2 can never read Host 1's localStorage.
 *   Instead, the failover redirect URL carries ?vlab_primary=<Host1_origin>.
 *   Host 2 reads this param, stashes it in sessionStorage (tab-scoped, same origin),
 *   and probes Host 1 every 15 s until it responds — then redirects back.
 */

(function () {
  "use strict";

  var currentInterval = 4000;  // 4 seconds base for fast failover
  var minInterval = 3000;      // 3 seconds min
  var maxInterval = 15000;     // 15 seconds max
  var pollTimer = null;
  var isSyncing = false;

  // ── Standby URL (for failover Host1 → Host2) ───────────────────────────────
  var cachedStandbyUrl = window.VLAB_INITIAL_STANDBY_URL || localStorage.getItem("vlab_standby_controller_url") || "";
  if (window.VLAB_INITIAL_STANDBY_URL) {
    try { localStorage.setItem("vlab_standby_controller_url", window.VLAB_INITIAL_STANDBY_URL); } catch (e) {}
  }

  // ── Primary URL (for failback Host2 → Host1) ───────────────────────────────
  // On failover redirect we append ?vlab_primary=<Host1_origin> to the URL.
  // Host 2 reads it here, saves to sessionStorage, and cleans the URL.
  // sessionStorage survives page refreshes within the same tab.
  var _params = null;
  try { _params = new URLSearchParams(window.location.search); } catch (e) {}
  var _primaryFromUrl = _params ? (_params.get("vlab_primary") || "") : "";
  if (_primaryFromUrl) {
    try { sessionStorage.setItem("vlab_primary_url", _primaryFromUrl); } catch (e) {}
    // Clean ?vlab_primary from the visible URL
    try {
      _params.delete("vlab_primary");
      var _qs = _params.toString();
      history.replaceState(null, "", window.location.pathname + (_qs ? "?" + _qs : ""));
    } catch (e) {}
  }
  var cachedPrimaryUrl = _primaryFromUrl
    || (typeof sessionStorage !== "undefined" ? sessionStorage.getItem("vlab_primary_url") || "" : "")
    || "";

  var offlineFailureCount = 0;
  var failoverRedirecting = false;
  var failbackCheckTimer = null;
  var failbackRedirecting = false;

  // If this tab already has a primary URL stored (page refresh on Host 2 after failover),
  // kick off the probe immediately — no need to wait for a health response.
  if (cachedPrimaryUrl && cachedPrimaryUrl !== window.location.origin) {
    failbackCheckTimer = setTimeout(probePrimary, 8000);
  }

  // ── UI badge states ─────────────────────────────────────────────────────────
  var States = {
    CHECKING: "CHECKING",
    ONLINE_SYNCED: "ONLINE_SYNCED",
    SYNCING: "SYNCING",
    LAN_LOCAL: "LAN_LOCAL",
    HOST_OFFLINE: "HOST_OFFLINE",
    ERROR: "ERROR",
  };

  function updateBadgeUI(state, details) {
    var badge = document.getElementById("vlabConnectivityBadge");
    var textEl = document.getElementById("vlabConnectivityText");
    var dotEl  = document.getElementById("vlabConnectivityDot");

    if (!badge || !textEl) return;

    badge.className = "connectivity-badge";
    badge.style.display = "inline-flex";
    badge.style.alignItems = "center";
    badge.style.gap = "6px";
    badge.style.padding = "4px 10px";
    badge.style.fontSize = "0.75rem";
    badge.style.fontWeight = "700";
    badge.style.borderRadius = "4px";
    badge.style.border = "2px solid #121212";
    badge.style.boxShadow = "2px 2px 0px #121212";
    badge.style.cursor = "default";
    badge.style.whiteSpace = "nowrap";
    badge.style.flexShrink = "0";
    badge.style.transition = "all 0.2s ease";

    if (dotEl) {
      dotEl.style.width = "8px";
      dotEl.style.height = "8px";
      dotEl.style.borderRadius = "50%";
      dotEl.style.display = "inline-block";
      dotEl.style.flexShrink = "0";
    }

    if (state === States.ONLINE_SYNCED) {
      badge.style.background = "#e6f9e6";
      badge.style.color = "#155724";
      if (dotEl) dotEl.style.background = "#28a745";
      textEl.textContent = "Online & Synced";
      badge.title = "Connected to local host & synchronized to Cloud PostgreSQL.";
    } else if (state === States.SYNCING) {
      badge.style.background = "#fff8e1";
      badge.style.color = "#856404";
      if (dotEl) dotEl.style.background = "#ffc107";
      var count = details && details.pending !== undefined ? details.pending : "";
      textEl.textContent = count ? "Syncing (" + count + ")" : "Syncing...";
      badge.title = "Local changes are actively syncing to cloud.";
    } else if (state === States.LAN_LOCAL) {
      badge.style.background = "#e8f4fd";
      badge.style.color = "#0d47a1";
      if (dotEl) dotEl.style.background = "#1976d2";
      var pendingCount = details && details.pending ? " (" + details.pending + ")" : "";
      textEl.textContent = "LAN Mode" + pendingCount;
      badge.title = "Connected to local lab server. Work is safely stored in local SQLite.";
    } else if (state === States.HOST_OFFLINE) {
      badge.style.background = "#ffebee";
      badge.style.color = "#721c24";
      if (dotEl) dotEl.style.background = "#dc3545";
      textEl.textContent = "Host Unreachable";
      badge.title = "Cannot reach local lab server. Check your Wi-Fi or LAN connection.";
    } else if (state === States.ERROR) {
      badge.style.background = "#fce4ec";
      badge.style.color = "#c2185b";
      if (dotEl) dotEl.style.background = "#e91e63";
      textEl.textContent = "Sync Warning";
      badge.title = (details && details.error) || "Sync issues detected.";
    } else {
      badge.style.background = "#f5f5f5";
      badge.style.color = "#555";
      if (dotEl) dotEl.style.background = "#888";
      textEl.textContent = "Checking...";
      badge.title = "Checking V-Lab cluster reachability...";
    }
  }

  // ── Main health poll ────────────────────────────────────────────────────────
  function checkHealth() {
    var controller = window.AbortController ? new AbortController() : null;
    var signal = controller ? controller.signal : null;
    var timeoutId = controller
      ? setTimeout(function () { controller.abort(); }, 2500)
      : null;

    fetch("/api/health", { method: "GET", cache: "no-store", signal: signal })
      .then(function (res) {
        if (timeoutId) clearTimeout(timeoutId);
        if (!res.ok && res.status !== 503) {
          throw new Error("HTTP " + res.status);
        }
        return res.json();
      })
      .then(function (data) {
        currentInterval = minInterval;
        offlineFailureCount = 0;

        // Update standby URL cache
        if (data.standby_controller_url) {
          cachedStandbyUrl = data.standby_controller_url;
          try { localStorage.setItem("vlab_standby_controller_url", cachedStandbyUrl); } catch (e) {}
        }

        // ── Failback logic ────────────────────────────────────────────────────
        // Case A: server explicitly tells us where primary is (optional hint)
        if (data.primary_controller_url) {
          var ep = data.primary_controller_url.replace(/\/+$/, "");
          if (ep && ep !== window.location.origin) {
            cachedPrimaryUrl = ep;
            try { sessionStorage.setItem("vlab_primary_url", ep); } catch (e) {}
          }
        }
        // Case B: we already have a primary URL from URL param or sessionStorage
        // (this is the main path — no server cooperation needed)
        if (cachedPrimaryUrl && cachedPrimaryUrl !== window.location.origin) {
          schedulePrimaryProbe();
        }

        // Badge logic
        var pending = data.pending_sync_count || 0;
        var cloudConfigured = Boolean(data.cloud_configured);
        var cloudReachable  = Boolean(data.cloud_reachable);
        var cloudStatus     = data.cloud_sync_status || "IDLE";

        if (!cloudConfigured || !cloudReachable || cloudStatus === "OFFLINE") {
          updateBadgeUI(States.LAN_LOCAL, { pending: pending });
        } else if (pending > 0 || cloudStatus === "PENDING" || cloudStatus === "SYNCING") {
          updateBadgeUI(States.SYNCING, { pending: pending });
        } else if (cloudStatus === "ERROR" || cloudStatus === "PARTIAL_ERROR") {
          updateBadgeUI(States.ERROR, { error: data.cloud_last_error || "Some sync events failed retry." });
        } else if (cloudReachable && pending === 0 && cloudStatus === "SYNCED") {
          updateBadgeUI(States.ONLINE_SYNCED);
        } else {
          updateBadgeUI(States.LAN_LOCAL, { pending: pending });
        }
      })
      .catch(function (err) {
        if (timeoutId) clearTimeout(timeoutId);
        offlineFailureCount++;
        updateBadgeUI(States.HOST_OFFLINE);
        currentInterval = Math.min(currentInterval * 1.5, maxInterval);

        // ── Auto-failover: Host 1 is down → redirect to Host 2 ───────────────
        if (offlineFailureCount >= 2 && cachedStandbyUrl && !failoverRedirecting) {
          var targetHost = cachedStandbyUrl.replace(/\/+$/, "");
          if (window.location.origin !== targetHost) {
            failoverRedirecting = true;
            // ★ KEY: embed current origin as ?vlab_primary so Host 2 knows where to failback
            var failoverUrl = targetHost
              + window.location.pathname
              + "?vlab_primary=" + encodeURIComponent(window.location.origin);

            if (window.Swal) {
              window.Swal.fire({
                icon: "warning",
                title: "Primary Host Offline",
                html: "Primary lab server (Host 1) became unreachable.<br><b>Auto-switching to Standby Host 2...</b>",
                timer: 4000,
                timerProgressBar: true,
                showConfirmButton: false,
                allowOutsideClick: false
              }).then(function () {
                window.location.href = failoverUrl;
              });
            } else {
              setTimeout(function () { window.location.href = failoverUrl; }, 2000);
            }
          }
        }
      })
      .finally(function () {
        scheduleNextPoll();
      });
  }

  // ── Failback probe ──────────────────────────────────────────────────────────
  function schedulePrimaryProbe() {
    if (failbackCheckTimer || failbackRedirecting) return; // already scheduled or done
    failbackCheckTimer = setTimeout(probePrimary, 15000);
  }

  function probePrimary() {
    failbackCheckTimer = null;
    if (!cachedPrimaryUrl || failbackRedirecting) return;
    var primaryOrigin = cachedPrimaryUrl.replace(/\/+$/, "");
    if (window.location.origin === primaryOrigin) return; // already on primary

    var ctrl = window.AbortController ? new AbortController() : null;
    var sig  = ctrl ? ctrl.signal : undefined;
    var tId  = ctrl ? setTimeout(function () { ctrl.abort(); }, 3000) : null;

    fetch(primaryOrigin + "/api/health", { method: "GET", cache: "no-store", signal: sig })
      .then(function (res) {
        if (tId) clearTimeout(tId);
        return res.json();
      })
      .then(function (data) {
        if (data && (data.status === "healthy" || data.status === "degraded")) {
          // ✅ Host 1 is back — trigger failback
          failbackRedirecting = true;
          var returnUrl = primaryOrigin + window.location.pathname;
          if (window.Swal) {
            window.Swal.fire({
              icon: "success",
              title: "Primary Host Back Online!",
              html: "Host 1 (Primary) has recovered.<br><b>Auto-switching back to Primary Host...</b>",
              timer: 4000,
              timerProgressBar: true,
              showConfirmButton: false,
              allowOutsideClick: false
            }).then(function () {
              try { sessionStorage.removeItem("vlab_primary_url"); } catch (e) {}
              window.location.href = returnUrl;
            });
          } else {
            setTimeout(function () {
              try { sessionStorage.removeItem("vlab_primary_url"); } catch (e) {}
              window.location.href = returnUrl;
            }, 2000);
          }
        } else {
          // Still not healthy — probe again later
          failbackCheckTimer = setTimeout(probePrimary, 15000);
        }
      })
      .catch(function () {
        if (tId) clearTimeout(tId);
        // Still unreachable — probe again later
        failbackCheckTimer = setTimeout(probePrimary, 15000);
      });
  }

  // ── Poll scheduler ──────────────────────────────────────────────────────────
  function scheduleNextPoll() {
    if (pollTimer) clearTimeout(pollTimer);
    var jitter = 0.8 + Math.random() * 0.4;
    var delay  = Math.round(currentInterval * jitter);
    pollTimer  = setTimeout(checkHealth, delay);
  }

  // ── Manual sync ─────────────────────────────────────────────────────────────
  window.triggerManualSync = function (event) {
    if (event) event.preventDefault();
    if (isSyncing) return;

    isSyncing = true;
    var syncIcon = document.getElementById("vlabSyncIcon");
    if (syncIcon) syncIcon.classList.add("fa-spin");
    updateBadgeUI(States.SYNCING);

    fetch("/api/sync/now", { method: "POST", headers: { "Content-Type": "application/json" } })
      .then(function (res) { return res.json(); })
      .then(function (resData) {
        if (window.Swal) {
          window.Swal.fire({
            icon: resData.ok ? "success" : "info",
            title: resData.ok ? "Sync Complete" : "Sync Notification",
            text: resData.message || (resData.ok ? "All pending events synced." : "Outbox empty or cloud offline."),
            timer: 2500,
            showConfirmButton: false,
            toast: true,
            position: "top-end",
          });
        }
      })
      .catch(function () {
        if (window.Swal) {
          window.Swal.fire({
            icon: "warning",
            title: "Offline Notice",
            text: "Could not reach local server to force sync.",
            timer: 2500,
            showConfirmButton: false,
            toast: true,
            position: "top-end",
          });
        }
      })
      .finally(function () {
        isSyncing = false;
        if (syncIcon) syncIcon.classList.remove("fa-spin");
        checkHealth();
      });
  };

  // ── Browser online/offline hints ─────────────────────────────────────────────
  window.addEventListener("online", function () {
    currentInterval = minInterval;
    checkHealth();
  });
  window.addEventListener("offline", function () {
    updateBadgeUI(States.HOST_OFFLINE);
  });

  document.addEventListener("DOMContentLoaded", function () {
    updateBadgeUI(States.CHECKING);
    checkHealth();
  });
})();
