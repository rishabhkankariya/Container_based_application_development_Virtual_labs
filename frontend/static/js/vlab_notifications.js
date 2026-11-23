/**
 * V-Lab SweetAlert2 Notification Helper & Global HA Response Interceptor
 * Provides user-friendly alerts, toasts, and diagnostic modals for:
 * - Automatic Failovers (Host 1 -> Host 2)
 * - Workload Spillover / Resource Balancing
 * - Connection Failures & Network Timeouts
 * - Port Clashes & Self-Healing Actions
 */

(function () {
    function hasSwal() {
        return typeof Swal !== 'undefined';
    }

    const Toast = hasSwal() ? Swal.mixin({
        toast: true,
        position: 'top-end',
        showConfirmButton: false,
        timer: 4500,
        timerProgressBar: true,
        didOpen: (toast) => {
            toast.addEventListener('mouseenter', Swal.stopTimer);
            toast.addEventListener('mouseleave', Swal.resumeTimer);
        }
    }) : null;

    window.VLabNotify = {
        /**
         * Quick non-intrusive toast notification
         */
        toast: function (icon, title, message) {
            if (!hasSwal()) {
                console.log(`[${(icon || 'info').toUpperCase()}] ${title}: ${message}`);
                return;
            }
            Toast.fire({
                icon: icon || 'info',
                title: title,
                text: message || ''
            });
        },

        /**
         * Displays a prominent alert when an automatic failover occurs
         */
        showFailoverAlert: function (fromHost, toHost, reason) {
            if (!hasSwal()) {
                alert(`Failover: Workload moved from ${fromHost} to ${toHost}.\n${reason}`);
                return;
            }
            Swal.fire({
                icon: 'warning',
                title: '🔄 Automatic Failover Active',
                html: `
                    <div style="text-align: left; font-size: 0.95rem; line-height: 1.5;">
                        <p style="margin-bottom: 8px;">
                            <strong>Node ${fromHost || 'Host 1 (Primary)'}</strong> became unreachable.
                        </p>
                        <p style="margin-bottom: 8px; color: #0275d8;">
                            ✨ Your session and workspace files have been <strong>safely restored on ${toHost || 'Host 2 (Hot Standby)'}</strong>.
                        </p>
                        <div style="background: #f8f9fa; border-left: 3px solid #f0ad4e; padding: 8px 12px; margin-top: 10px; font-size: 0.85rem; color: #555;">
                            <strong>Diagnostics:</strong> ${reason || 'Heartbeat timeout detected on primary compute node.'}
                        </div>
                    </div>
                `,
                confirmButtonText: 'Continue Lab Work',
                confirmButtonColor: '#0275d8'
            });
        },

        /**
         * Displays a toast when workload was scheduled on standby node due to high load
         */
        showSpilloverAlert: function (primaryHost, assignedHost, reason) {
            this.toast(
                'info',
                '⚖️ Load Balancing Active',
                `Primary (${primaryHost}) is near capacity. Workload scheduled on ${assignedHost}.`
            );
        },

        /**
         * Displays a comprehensive diagnostic modal for network/connection errors
         */
        showConnectionError: function (errCode, title, message, troubleshooting) {
            if (!hasSwal()) {
                alert(`Error [${errCode}]: ${message}`);
                return;
            }
            Swal.fire({
                icon: 'error',
                title: title || 'Connection Failure',
                html: `
                    <div style="text-align: left; font-size: 0.92rem;">
                        <div style="display: inline-block; background: #ffebee; color: #c62828; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-family: monospace; font-size: 0.85rem; margin-bottom: 10px;">
                            ${errCode || 'ERR_NETWORK'}
                        </div>
                        <p style="margin-bottom: 12px; color: #333;">${message || 'Could not communicate with compute node.'}</p>
                        ${troubleshooting ? `
                            <div style="background: #f4f6f8; border-radius: 6px; padding: 10px; font-size: 0.82rem; color: #444; border: 1px solid #e0e0e0;">
                                <strong style="color: #222;">🔧 Recommended Action:</strong><br>
                                ${troubleshooting}
                            </div>
                        ` : ''}
                    </div>
                `,
                confirmButtonText: 'Dismiss',
                confirmButtonColor: '#6c757d'
            });
        },

        /**
         * Success toast
         */
        showSuccess: function (title, message) {
            this.toast('success', title, message);
        }
    };

    // Global fetch interceptor for all V-Lab API calls
    const originalFetch = window.fetch;
    window.fetch = async function (...args) {
        const response = await originalFetch.apply(this, args);
        try {
            const clone = response.clone();
            const contentType = clone.headers.get("content-type");
            if (contentType && contentType.includes("application/json")) {
                clone.json().then(data => {
                    if (!data) return;
                    if (data.failover_notice) {
                        window.VLabNotify.showFailoverAlert(
                            data.failover_notice.from_host,
                            data.failover_notice.to_host,
                            data.failover_notice.message || data.failover_notice.reason
                        );
                    } else if (data.spillover_notice) {
                        window.VLabNotify.showSpilloverAlert(
                            data.spillover_notice.primary_host,
                            data.spillover_notice.assigned_host,
                            data.spillover_notice.reason
                        );
                    } else if (data.error_code && !data.ok) {
                        window.VLabNotify.showConnectionError(
                            data.error_code,
                            data.error_title || "Execution Notice",
                            data.error_message || data.output,
                            data.troubleshooting
                        );
                    }
                }).catch(() => {});
            }
        } catch (e) {}
        return response;
    };
})();
