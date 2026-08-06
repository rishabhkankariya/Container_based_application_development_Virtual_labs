# Summary of Project Modifications & Enhancements

This document outlines all features, UI/UX upgrades, and structural changes made to the **Container-Based Application Development Virtual Labs** codebase.

---

## 🛠️ 1. Backend Enhancements (`app.py`)

- **Disable Static & Template Caching**:
  - Configured `TEMPLATES_AUTO_RELOAD = True` and `SEND_FILE_MAX_AGE_DEFAULT = 0` to ensure real-time UI/CSS updates render without stale browser caching.
- **Manual Student Registration Endpoint (`/faculty/add-student`)**:
  - Added HTTP `POST` endpoint allowing faculty to register individual students manually.
  - Automatically generates next sequential Student ID (e.g. `STU005`), secure default password (e.g. `Docker@4819`), and password hashes (`generate_password_hash`).
- **Forgot Password Workflow Removal**:
  - Removed deprecated `/forgot-password` endpoint to streamline account security management.

---

## 🎨 2. Design System & Theme Engine (`static/css/style.css`)

- **Dual-Theme Engine (Dark Futuristic & Light Mode)**:
  - Configured CSS custom properties (`--bg`, `--surface`, `--border`, `--text`, `--blue`, `--green`, `--purple`, etc.) matching both **Dark Cyberpunk Futuristic Mode** and **Clean Light Mode**.
- **Dark Mode Search Text Visibility Fix**:
  - Included `input[type="search"]` in global input selectors with `color: var(--text) !important;` and `caret-color: var(--blue)`, ensuring typed search text is vibrant and readable in dark mode.
- **Roster Toolbar & Sortable Header Styling**:
  - Created modern styles for `.roster-toolbar`, `.roster-search-wrapper`, clear button (`.clear-search-btn`), dropdown selects (`.roster-select`), reset button, `.roster-count-badge`, and interactive sortable headers (`.sortable-header`, `.sort-icon`).
- **Browser Autofill Style Override**:
  - Implemented strict `-webkit-autofill` rules to prevent Chrome/Edge from overriding dark/light input fields with default white/blue backgrounds.

---

## 🌐 3. Global Framework (`templates/base.html`)

- **Interactive Particle System**:
  - Upgraded background canvas (`#particles`) with interactive mouse-connected glowing nodes that respond to cursor movement.
- **Theme Switcher Script & Anti-Flash Initialization**:
  - Added an inline script in `<head>` to read `localStorage` (`vlab-theme`) and set `data-theme` before rendering, eliminating page flashes.
  - Defined global `window.toggleVLabTheme()` and `window.togglePassword(btn)` helpers.

---

## 📊 4. Faculty Dashboard & Student Roster (`templates/faculty_dashboard.html`)

- **Real-Time Student Search**:
  - Added an instant search bar matching student Full Name, Student ID, or Email with a quick-clear (`X`) button.
- **Alphabetical & Multi-Column Sorting**:
  - Added dropdown sorting options: Name (A &rarr; Z), Name (Z &rarr; A), Student ID (Asc/Desc), Overall Score (High &rarr; Low / Low &rarr; High), and Password Status.
  - Interactive clickable column headers (`Student ID`, `Name`, `Overall Score`) with `aria-sort` accessibility attributes and `▲` / `▼` sort direction indicators.
- **Multi-Criteria Dropdown Filters**:
  - Dropdown filters for Password Status (Default/Changed), Lab 1 Progress (Completed/In Progress/Not Started), Lab 2 Progress, and a one-click Reset Filters button.
- **Dynamic Student Counter Badge (`#rosterCountBadge`)**:
  - Live counter displaying `Showing X of Y Students` calculated dynamically from DOM row counts (`totalStudentsCount = rows.length`), eliminating global `window.totalCount` DOM collision bugs (`Showing X of [object HTMLSpanElement]`).
- **Manual Student Enrollment Card**:
  - Positioned the "Add Student Manually" card directly above the Student Roster section.
  - Faculty inputs Student Name and Email/Gmail. Submits via AJAX (`submitManualStudent`), auto-generates credentials, dynamically appends the new student row to the roster table, and updates total student count stat badges without a page reload.

---

## 📊 5. Student Dashboard (`templates/student_dashboard.html`)

- **Hero Banner**:
  - Added a glassmorphic welcome section with gradient text and student metadata pill badges.
- **Per-Lab Metrics Breakdown (`.grid-4`)**:
  - Split lab progress into a 4-column metric view:
    - **Lab 1 Status**: Dedicated status box showing stage count (`7/7 steps completed`).
    - **Lab 2 Status**: Dedicated status box showing stage count (`0/5 steps completed`).
    - **Total Steps**: Combined step counter (`7/12`).
    - **Overall Progress**: Dynamic percentage indicator (`58%`).

---

## 🔐 6. Password Visibility & Security Views

- **`login.html`**:
  - Removed "Forgot Password?" link.
  - Added native inline SVG eye / eye-slash password visibility toggle buttons.
- **`change_password.html`**:
  - Integrated eye visibility buttons for initial login password updates.
- **`lab1.html` & `lab2.html`**:
  - Added theme switcher controls and password toggle support for Docker Hub fields.

---

## 🚀 How to Run the Application

```bash
# Run Flask Server (Listens on 0.0.0.0:5000)
python app.py
```
- Local Host: **[http://127.0.0.1:5000](http://127.0.0.1:5000)**
- LAN / Wi-Fi Access: **`http://<YOUR_LOCAL_IP>:5000`** (e.g. `http://10.123.210.185:5000`)
