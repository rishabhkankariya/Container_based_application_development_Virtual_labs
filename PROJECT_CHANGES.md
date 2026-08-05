# Summary of Project Modifications & Enhancements

This document outlines all features, UI/UX upgrades, and structural changes made to the **Container-Based Application Development Virtual Labs** codebase.

---

## 🛠️ 1. Backend Enhancements (`backend/app.py`)

- **Disable Static & Template Caching**:
  - Configured `TEMPLATES_AUTO_RELOAD = True` and `SEND_FILE_MAX_AGE_DEFAULT = 0` to ensure real-time UI/CSS updates render without stale browser caching.
- **Forgot Password Workflow (`/forgot-password`)**:
  - Added a dedicated HTTP `GET`/`POST` endpoint for student and faculty password recovery.
  - Implemented role-based account validation (Student ID or Faculty Username) with secure password hashing (`generate_password_hash`) in `database/vlab.db`.

---

## 🎨 2. Design System & Theme Engine (`frontend/static/css/style.css`)

- **Dual-Theme Engine (Dark Futuristic & Light Mode)**:
  - Configured CSS custom properties (`--bg`, `--surface`, `--border`, `--text`, `--blue`, `--green`, `--purple`, etc.) matching both **Dark Cyberpunk Futuristic Mode** and **Clean Light Mode**.
- **Browser Autofill Style Override**:
  - Implemented strict `-webkit-autofill` rules to prevent Chrome/Edge from overriding dark/light input fields with default white/blue backgrounds.
- **Glassmorphism UI Components**:
  - Upgraded `.card`, `.stat`, `.hero-banner`, `.role-toggle`, `.topbar`, and `.badge` with glassmorphic backdrop filters, soft shadows, and hover animations.

---

## 🌐 3. Global Framework (`frontend/templates/base.html`)

- **Interactive Particle System**:
  - Upgraded background canvas (`#particles`) with interactive mouse-connected glowing nodes that respond to cursor movement.
- **Theme Switcher Script & Anti-Flash Initialization**:
  - Added an inline script in `<head>` to read `localStorage` (`vlab-theme`) and set `data-theme` before rendering, eliminating page flashes.
  - Defined global `window.toggleVLabTheme()` and `window.togglePassword(btn)` helpers.

---

## 📊 4. Student Dashboard (`frontend/templates/student_dashboard.html`)

- **Hero Banner**:
  - Added a glassmorphic welcome section with gradient text and student metadata pill badges.
- **Per-Lab Metrics Breakdown (`.grid-4`)**:
  - Split lab progress into a 4-column metric view:
    - **Lab 1 Status**: Dedicated status box showing stage count (`7/7 steps completed`).
    - **Lab 2 Status**: Dedicated status box showing stage count (`0/5 steps completed`).
    - **Total Steps**: Combined step counter (`7/12`).
    - **Overall Progress**: Dynamic percentage indicator (`58%`).

---

## 🔐 5. Password Visibility & Security Views

- **`login.html`**:
  - Added "Forgot Password?" navigation link.
  - Added native inline SVG eye / eye-slash password visibility toggle buttons.
- **`forgot_password.html`**:
  - Built password reset page with Student / Faculty role toggle and eye visibility buttons.
- **`change_password.html`**:
  - Integrated eye visibility buttons for initial login password updates.
- **`lab1.html` & `lab2.html`**:
  - Added theme switcher controls and password toggle support for Docker Hub fields.

---

## 🚀 How to Run the Application

```bash
# Run Flask Server
python backend/app.py
```
Open **[http://localhost:5000](http://localhost:5000)** in your browser.
