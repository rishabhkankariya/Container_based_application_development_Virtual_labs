#!/usr/bin/env bash
# ==============================================================================
# V-Lab Unified Launcher (POSIX / Linux / macOS / Container Environments)
# Single Point of Access for Host 1, Host 2, and Simulation
# ==============================================================================
set -e

# Change directory to repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"

# 1. Runtime Discovery (Python 3)
if [ -n "$PYTHON_EXE" ] && command -v "$PYTHON_EXE" >/dev/null 2>&1; then
    PY_CMD="$PYTHON_EXE"
elif [ -f "venv/bin/python" ]; then
    PY_CMD="venv/bin/python"
elif [ -f ".venv/bin/python" ]; then
    PY_CMD=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PY_CMD="python"
else
    echo "========================================================================"
    echo " [ERROR] Python 3 runtime was not detected."
    echo " Please install Python 3.10+ in your environment."
    echo "========================================================================"
    exit 1
fi

# 2. Local IP Discovery
if [ -z "$LAN_IP" ]; then
    if command -v hostname >/dev/null 2>&1 && hostname -I >/dev/null 2>&1; then
        LAN_IP=$(hostname -I | awk '{print $1}')
    elif command -v ip >/dev/null 2>&1; then
        LAN_IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{print $7}')
    elif command -v ifconfig >/dev/null 2>&1; then
        LAN_IP=$(ifconfig | grep -Eo 'inet (addr:)?([0-9]*\.){3}[0-9]*' | grep -Eo '([0-9]*\.){3}[0-9]*' | grep -v '127.0.0.1' | head -n 1)
    fi
    LAN_IP="${LAN_IP:-127.0.0.1}"
fi

MODE="${1:-}"

run_host1() {
    clear 2>/dev/null || true
    echo "========================================================================"
    echo "  [+] Starting HOST 1 (PRIMARY) + Central Controller"
    echo "========================================================================"
    echo "  Local Web Access:    http://localhost:5000"
    echo "  Network LAN Access:  http://$LAN_IP:5000"
    echo "  Compute Agent:       HOST1 on Port 5051"
    echo ""
    echo "  On Host 2 (Standby PC), point controller URL to:"
    echo "  http://$LAN_IP:5000"
    echo "========================================================================"
    echo ""
    # Start Agent in background
    "$PY_CMD" agent/vlab_agent.py --host-id HOST1 --role PRIMARY --controller-url http://127.0.0.1:5000 --agent-port 5051 &
    AGENT_PID=$!
    trap 'kill $AGENT_PID 2>/dev/null || true' EXIT INT TERM
    # Run Controller in foreground
    "$PY_CMD" backend/app.py
}

run_host2() {
    clear 2>/dev/null || true
    echo "========================================================================"
    echo "  [*] Starting HOST 2 (HOT STANDBY NODE)"
    echo "========================================================================"
    CONFIG_FILE="host_node_config.txt"
    CONTROLLER_URL="${2:-}"
    if [ -z "$CONTROLLER_URL" ] && [ -f "$CONFIG_FILE" ]; then
        CONTROLLER_URL=$(grep -E '^CONTROLLER_URL=' "$CONFIG_FILE" | cut -d '=' -f2-)
    fi
    if [ -z "$CONTROLLER_URL" ]; then
        echo ""
        echo "Enter Host 1 Controller URL as shown on Host 1's screen:"
        echo "(Example: http://192.168.1.100:5000 or http://127.0.0.1:5000)"
        read -r -p "Controller URL: " CONTROLLER_URL
        CONTROLLER_URL="${CONTROLLER_URL:-http://127.0.0.1:5000}"
        echo "CONTROLLER_URL=$CONTROLLER_URL" > "$CONFIG_FILE"
    else
        echo "Connected Controller: $CONTROLLER_URL"
    fi
    echo ""
    echo "Launching Host 2 Standby Agent on port 5052..."
    echo "========================================================================"
    "$PY_CMD" agent/vlab_agent.py --host-id HOST2 --role HOT_STANDBY --controller-url "$CONTROLLER_URL" --agent-port 5052
}

run_demo() {
    clear 2>/dev/null || true
    echo "========================================================================"
    echo "  [!] Starting Complete Local Simulation (Controller + Host 1 + Host 2)"
    echo "========================================================================"
    echo "  Host 1 Agent:  Port 5051 (Primary)"
    echo "  Host 2 Agent:  Port 5052 (Hot Standby)"
    echo "  Controller:    Port 5000"
    echo "========================================================================"
    echo ""
    "$PY_CMD" agent/vlab_agent.py --host-id HOST1 --role PRIMARY --controller-url http://127.0.0.1:5000 --agent-port 5051 &
    A1_PID=$!
    "$PY_CMD" agent/vlab_agent.py --host-id HOST2 --role HOT_STANDBY --controller-url http://127.0.0.1:5000 --agent-port 5052 &
    A2_PID=$!
    trap 'kill $A1_PID $A2_PID 2>/dev/null || true' EXIT INT TERM
    echo "Starting Controller..."
    "$PY_CMD" backend/app.py
}

run_controller() {
    clear 2>/dev/null || true
    echo "========================================================================"
    echo "  [+] Starting Web Controller Only (Port 5000)"
    echo "========================================================================"
    echo "  Local Web Access:    http://localhost:5000"
    echo "  Network LAN Access:  http://$LAN_IP:5000"
    echo "========================================================================"
    echo ""
    "$PY_CMD" backend/app.py
}

case "$MODE" in
    1|host1|primary)
        run_host1
        ;;
    2|host2|standby)
        run_host2 "$@"
        ;;
    3|demo|sim)
        run_demo
        ;;
    4|controller)
        run_controller
        ;;
    5|exit)
        exit 0
        ;;
    *)
        clear 2>/dev/null || true
        echo "========================================================================"
        echo "  [V-Lab] Container-Based Virtual Lab - Unified Launcher"
        echo "========================================================================"
        echo "  Detected Network IP:   $LAN_IP"
        echo "  Python Runtime:        $PY_CMD"
        echo "========================================================================"
        echo ""
        echo "  Select the role for THIS environment:"
        echo ""
        echo "  [1] HOST 1 (PRIMARY) - Main Lab Machine [RECOMMENDED]"
        echo "      * Starts Central Web Controller (Port 5000)"
        echo "      * Starts Host 1 Compute Node Agent (Port 5051)"
        echo "      * Runs all student Docker workloads"
        echo ""
        echo "  [2] HOST 2 (HOT STANDBY) - Secondary Lab Machine"
        echo "      * Starts Standby Agent (Port 5052)"
        echo "      * Pre-caches Docker images from Docker Hub"
        echo "      * Stands ready for instant failover if Host 1 goes offline"
        echo ""
        echo "  [3] LOCAL SIMULATION / DEMO (Single Machine)"
        echo "      * Starts Controller + Host 1 + Host 2 all on this machine"
        echo "      * Allows testing automatic failover and SweetAlerts right now"
        echo ""
        echo "  [4] WEB CONTROLLER ONLY (Port 5000)"
        echo ""
        echo "  [5] EXIT"
        echo ""
        echo "========================================================================"
        read -r -p "Enter choice [1-5] (Default is 1): " CHOICE
        CHOICE="${CHOICE:-1}"
        case "$CHOICE" in
            1) run_host1 ;;
            2) run_host2 ;;
            3) run_demo ;;
            4) run_controller ;;
            5) exit 0 ;;
            *) echo "Invalid choice. Exiting."; exit 1 ;;
        esac
        ;;
esac
