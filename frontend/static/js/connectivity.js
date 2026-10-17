/**
 * V-Lab Cluster Connectivity & Cloud Sync Manager
 * Monitors /api/health with exponential backoff & jitter.
 * Updates navigation badge and handles on-demand manual synchronization.
 */

(function () {
  "use strict";

  var currentInterval = 12000; // 12 seconds base
  var minInterval = 10000;     // 10 seconds min
  var maxInterval = 35000;     // 35 seconds max
  var pollTimer = null;
  var isSyncing = false;

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
    var dotEl = document.getElementById("vlabConnectivityDot");
    var syncBtn = document.getElementById("vlabSyncNowBtn");

    if (!badge || !textEl) return;

    // Reset styles
    badge.className = "connectivity-badge";
    badge.style.display = "inline-flex";
    badge.style.alignItems = "center";
    badge.style.gap = "6px";
    badge.style.padding = "4px 10px";
    badge.style.fontSize = "0.75rem";
    badge.style.fontWeight = "700";
    badge.style.borderRadius = "999px";
    badge.style.border = "2px solid #121212";
    badge.style.boxShadow = "2px 2px 0px #121212";
    badge.style.cursor = "default";
    badge.style.transition = "all 0.2s ease";

    if (dotEl) {
      dotEl.style.width = "8px";
      dotEl.style.height = "8px";
      dotEl.style.borderRadius = "50%";
      dotEl.style.display = "inline-block";
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
      var pendingCount = details && details.pending ? " (" + details.pending + " pending)" : "";
      textEl.textContent = "Local LAN Mode" + pendingCount;
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
      // CHECKING
      badge.style.background = "#f5f5f5";
      badge.style.color = "#555";
      if (dotEl) dotEl.style.background = "#888";
      textEl.textContent = "Checking...";
      badge.title = "Checking V-Lab cluster reachability...";
    }
  }

  function checkHealth() {
    var controller = window.AbortController ? new AbortController() : null;
    var signal = controller ? controller.signal : null;
    var timeoutId = controller
      ? setTimeout(function () {
          controller.abort();
        }, 5000)
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
        currentInterval = minInterval; // reset backoff
        var pending = data.pending_sync_count || 0;
        var cloudConfigured = Boolean(data.cloud_configured);
        var cloudStatus = data.cloud_sync_status || "IDLE";

        if (!cloudConfigured) {
          // Connected to local Flask server over LAN, but cloud PostgreSQL is not configured/offline
          updateBadgeUI(States.LAN_LOCAL, { pending: pending });
        } else if (pending > 0 || cloudStatus === "SYNCING") {
          updateBadgeUI(States.SYNCING, { pending: pending });
        } else if (cloudStatus === "PARTIAL_ERROR") {
          updateBadgeUI(States.ERROR, { error: "Some sync events failed retry." });
        } else {
          updateBadgeUI(States.ONLINE_SYNCED);
        }
      })
      .catch(function (err) {
        if (timeoutId) clearTimeout(timeoutId);
        // Server or LAN offline
        updateBadgeUI(States.HOST_OFFLINE);
        currentInterval = Math.min(currentInterval * 1.5, maxInterval);
      })
      .finally(function () {
        scheduleNextPoll();
      });
  }

  function scheduleNextPoll() {
    if (pollTimer) clearTimeout(pollTimer);
    // Apply jitter (+/- 20%)
    var jitter = 0.8 + Math.random() * 0.4;
    var delay = Math.round(currentInterval * jitter);
    pollTimer = setTimeout(checkHealth, delay);
  }

  window.triggerManualSync = function (event) {
    if (event) event.preventDefault();
    if (isSyncing) return;

    isSyncing = true;
    var syncIcon = document.getElementById("vlabSyncIcon");
    if (syncIcon) syncIcon.classList.add("fa-spin");

    updateBadgeUI(States.SYNCING);

    fetch("/api/sync/now", { method: "POST", headers: { "Content-Type": "application/json" } })
      .then(function (res) {
        return res.json();
      })
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
      .catch(function (err) {
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

  // Listen for browser online/offline events as immediate polling hints
  window.addEventListener("online", function () {
    currentInterval = minInterval;
    checkHealth();
  });

  window.addEventListener("offline", function () {
    updateBadgeUI(States.OFFLINE);
  });

  document.addEventListener("DOMContentLoaded", function () {
    updateBadgeUI(States.CHECKING);
    checkHealth();
  });
})();
