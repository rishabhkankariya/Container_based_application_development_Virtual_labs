/**
 * V-Lab Practice Playground Controller
 * Manages terminal execution, command history, environment switching,
 * and isolated workspace file editing.
 */

(function () {
  let activeSessionId = window.VLAB_PLAYGROUND_SESSION_ID || '';
  let activeEnvType = window.VLAB_PLAYGROUND_ENV || 'linux';
  let activePrompt = window.VLAB_PLAYGROUND_PROMPT || 'vlab@playground:~$ ';

  const cmdHistory = [];
  let historyIndex = -1;
  let isExecuting = false;

  // DOM Elements
  const termBody = document.getElementById('pgTermBody');
  const outputHistory = document.getElementById('pgOutputHistory');
  const termInput = document.getElementById('pgTermInput');
  const promptEl = document.getElementById('pgPrompt');
  const sessionStatusEl = document.getElementById('pgSessionStatus');
  const fileListEl = document.getElementById('pgFileList');
  const editorModal = document.getElementById('pgEditorModal');
  const editorFilename = document.getElementById('pgEditorFilename');
  const editorContent = document.getElementById('pgEditorContent');

  function init() {
    if (!termInput) return;

    // Focus input on terminal click
    termBody.addEventListener('click', () => {
      termInput.focus();
    });

    // Terminal keyboard navigation
    termInput.addEventListener('keydown', handleKeyDown);

    // Initial load of workspace files
    loadWorkspaceFiles();

    // Welcome message in terminal
    appendOutput(
      `🎉 Welcome to V-Lab Practice Playground [${activeEnvType.toUpperCase()} MODE]\n` +
      `Type commands below or click snippets in the Cheat Sheet on the left.\n` +
      `Workspace files are fully isolated in your private session.\n` +
      `------------------------------------------------------------------------\n`,
      'pg-line-success'
    );
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      const rawCmd = termInput.value;
      termInput.value = '';
      if (!rawCmd.trim()) return;

      cmdHistory.push(rawCmd);
      historyIndex = cmdHistory.length;
      runCommand(rawCmd);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (cmdHistory.length > 0 && historyIndex > 0) {
        historyIndex--;
        termInput.value = cmdHistory[historyIndex];
      }
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (cmdHistory.length > 0 && historyIndex < cmdHistory.length - 1) {
        historyIndex++;
        termInput.value = cmdHistory[historyIndex];
      } else {
        historyIndex = cmdHistory.length;
        termInput.value = '';
      }
    } else if (e.ctrlKey && e.key === 'l') {
      e.preventDefault();
      clearTerminal();
    }
  }

  function runCommand(command) {
    if (isExecuting) return;
    isExecuting = true;

    // Render executed line
    appendOutput(`${activePrompt}${command}`, 'pg-line-cmd');

    // Handle local clear
    const lower = command.trim().toLowerCase();
    if (lower === 'clear' || lower === 'cls') {
      clearTerminal();
      isExecuting = false;
      return;
    }

    // Render loading indicator
    const loaderId = 'loader-' + Date.now();
    appendOutput('⏳ Running...', 'pg-line-res', loaderId);

    fetch('/api/playground/exec', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: activeSessionId,
        command: command
      })
    })
      .then(res => res.json())
      .then(data => {
        removeLoader(loaderId);
        isExecuting = false;

        if (data.action === 'clear') {
          clearTerminal();
          return;
        }

        const cls = data.ok ? 'pg-line-res' : 'pg-line-error';
        const out = data.output || (data.ok ? 'Done.' : 'Command failed.');
        appendOutput(out, cls);

        // Refresh file list in case command created/modified files
        if (command.includes('touch') || command.includes('mkdir') || command.includes('rm') || command.includes('git') || command.includes('nano')) {
          loadWorkspaceFiles();
        }
      })
      .catch(err => {
        removeLoader(loaderId);
        isExecuting = false;
        appendOutput(`Network / Execution Error: ${err.message}`, 'pg-line-error');
      });
  }

  function appendOutput(text, className = '', id = '') {
    const line = document.createElement('div');
    if (className) line.className = className;
    if (id) line.id = id;
    line.textContent = text;
    outputHistory.appendChild(line);
    termBody.scrollTop = termBody.scrollHeight;
  }

  function removeLoader(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
  }

  window.clearTerminal = function () {
    outputHistory.innerHTML = '';
    termBody.scrollTop = 0;
  };

  // Environment switching
  window.switchPlaygroundEnv = function (envType) {
    if (envType === activeEnvType) return;

    appendOutput(`🔄 Switching to ${envType.toUpperCase()} environment...`, 'pg-line-cmd');

    fetch('/api/playground/session/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ env_type: envType })
    })
      .then(res => res.json())
      .then(data => {
        if (data.ok && data.session) {
          activeSessionId = data.session.session_id;
          activeEnvType = data.session.env_type;
          activePrompt = data.session.prompt;

          // Update UI prompt & tabs
          promptEl.textContent = activePrompt;
          document.querySelectorAll('.pg-env-btn').forEach(btn => {
            btn.classList.toggle('active', btn.getAttribute('data-env') === envType);
          });

          // Update header info
          const envNameEl = document.getElementById('pgActiveEnvName');
          if (envNameEl) envNameEl.textContent = data.session.env_name;

          const sidEl = document.getElementById('pgActiveSessionId');
          if (sidEl) sidEl.textContent = activeSessionId.substring(0, 10) + '...';

          // Update cheatsheet visibility
          document.querySelectorAll('.pg-cheatsheet-group').forEach(grp => {
            grp.style.display = grp.getAttribute('data-env') === envType ? 'block' : 'none';
          });

          clearTerminal();
          appendOutput(
            `🚀 Switched to ${data.session.env_name}.\n` +
            `${data.session.env_desc}\n` +
            `Virtual environment: /home/vlab (Isolated container sandbox)\n` +
            `------------------------------------------------------------------------\n`,
            'pg-line-success'
          );

          loadWorkspaceFiles();
        }
      })
      .catch(err => {
        appendOutput(`Failed to switch environment: ${err.message}`, 'pg-line-error');
      });
  };

  // Reset Session
  window.resetPlaygroundSession = function () {
    if (!confirm('Are you sure you want to reset this environment? All practice files in this session will be restored to clean defaults.')) {
      return;
    }

    appendOutput(`⚠️ Resetting session files to defaults...`, 'pg-line-cmd');

    fetch('/api/playground/session/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: activeSessionId })
    })
      .then(res => res.json())
      .then(data => {
        if (data.ok) {
          clearTerminal();
          appendOutput(`✅ ${data.message}\nStarter files re-seeded.`, 'pg-line-success');
          loadWorkspaceFiles();
        } else {
          appendOutput(`Reset Error: ${data.message}`, 'pg-line-error');
        }
      })
      .catch(err => {
        appendOutput(`Network error: ${err.message}`, 'pg-line-error');
      });
  };

  // File explorer & scratchpad editor
  function loadWorkspaceFiles() {
    if (!fileListEl) return;
    fileListEl.innerHTML = '<div style="padding:10px;color:#888;font-size:0.75rem">Loading files...</div>';

    fetch(`/api/playground/files?session_id=${encodeURIComponent(activeSessionId)}`)
      .then(res => res.json())
      .then(data => {
        if (data.ok && Array.isArray(data.files)) {
          if (data.files.length === 0) {
            fileListEl.innerHTML = '<div style="padding:10px;color:#888;font-size:0.75rem">Workspace is empty. Create a file or run a command.</div>';
            return;
          }

          fileListEl.innerHTML = data.files.map(f => {
            const icon = getFileIcon(f.name);
            const sz = f.size > 1024 ? Math.round(f.size / 1024) + ' KB' : f.size + ' B';
            return `
              <div class="pg-file-item" onclick="openFileEditor('${escapeHtml(f.name)}')">
                <span style="display:flex;align-items:center;gap:6px">
                  <i class="${icon}"></i> ${escapeHtml(f.name)}
                </span>
                <span style="font-size:0.7rem;color:#888">${sz}</span>
              </div>
            `;
          }).join('');
        }
      })
      .catch(() => {
        fileListEl.innerHTML = '<div style="padding:10px;color:#ef4444;font-size:0.75rem">Failed to load files</div>';
      });
  }

  function getFileIcon(filename) {
    if (filename.endsWith('.py')) return 'fa-brands fa-python text-blue-500';
    if (filename.endsWith('.sh')) return 'fa-solid fa-terminal text-yellow-500';
    if (filename.endsWith('.txt')) return 'fa-solid fa-file-lines text-slate-400';
    if (filename.endsWith('.csv')) return 'fa-solid fa-file-csv text-green-500';
    if (filename.endsWith('.md')) return 'fa-brands fa-markdown text-purple-500';
    if (filename.includes('Dockerfile')) return 'fa-brands fa-docker text-sky-400';
    if (filename.endsWith('.yml') || filename.endsWith('.yaml')) return 'fa-solid fa-file-code text-orange-400';
    return 'fa-solid fa-file text-slate-400';
  }

  window.openFileEditor = function (filename) {
    fetch(`/api/playground/file/read?session_id=${encodeURIComponent(activeSessionId)}&filename=${encodeURIComponent(filename)}`)
      .then(res => res.json())
      .then(data => {
        if (data.ok) {
          editorFilename.value = data.filename;
          editorContent.value = data.content;
          editorModal.classList.add('open');
          setTimeout(() => { if (editorContent) editorContent.focus(); }, 60);
        } else {
          alert('Could not read file: ' + (data.error || 'Unknown error'));
        }
      });
  };

  window.createNewFile = function () {
    const fname = prompt('Enter new filename (e.g. test.py, script.sh):');
    if (!fname) return;
    editorFilename.value = fname.trim();
    editorContent.value = '';
    editorModal.classList.add('open');
    setTimeout(() => { if (editorContent) editorContent.focus(); }, 60);
  };

  window.saveFileEditor = function () {
    const filename = editorFilename.value.trim();
    const content = editorContent.value;
    if (!filename) return;

    fetch('/api/playground/file/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: activeSessionId,
        filename: filename,
        content: content
      })
    })
      .then(res => res.json())
      .then(data => {
        if (data.ok) {
          closeFileEditor();
          loadWorkspaceFiles();
          appendOutput(`💾 File saved: ${filename}`, 'pg-line-success');
        } else {
          alert('Save failed: ' + (data.error || 'Unknown error'));
        }
      });
  };

  window.closeFileEditor = function () {
    if (editorModal) editorModal.classList.remove('open');
    if (termInput) termInput.focus();
  };

  // Close modal when clicking backdrop
  if (editorModal) {
    editorModal.addEventListener('click', (e) => {
      if (e.target === editorModal) closeFileEditor();
    });
  }

  // Handle Tab indentation inside code editor textarea
  if (editorContent) {
    editorContent.addEventListener('keydown', (e) => {
      if (e.key === 'Tab') {
        e.preventDefault();
        const start = editorContent.selectionStart;
        const end = editorContent.selectionEnd;
        editorContent.value = editorContent.value.substring(0, start) + '    ' + editorContent.value.substring(end);
        editorContent.selectionStart = editorContent.selectionEnd = start + 4;
      }
    });
  }

  // Global hotkeys for file editor modal (Ctrl+S to save, Escape to exit)
  window.addEventListener('keydown', (e) => {
    if (editorModal && editorModal.classList.contains('open')) {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeFileEditor();
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        saveFileEditor();
      }
    }
  });

  // Cheatsheet snippet paste
  window.pasteCommand = function (cmdText) {
    if (!termInput) return;
    termInput.value = cmdText;
    termInput.focus();
  };

  function escapeHtml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // Auto-init on load
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
