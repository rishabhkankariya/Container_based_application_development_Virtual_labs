/**
 * V-Lab Permanent Host Workspace Synchronization Client
 * Ensures student files are persisted permanently to the host machine disk
 * (workspaces/<student_id>/) and the central database (student_workspaces table).
 */
(function (global) {
  'use strict';

  let debounceTimer = null;

  function getStudentId() {
    return global.STUDENT_ID || (typeof STUDENT_ID !== 'undefined' ? STUDENT_ID : 'guest');
  }

  const VLabWorkspace = {
    /**
     * Retrieves all files permanently stored on the host machine for this student
     * @param {string} [studentId] Optional student ID override
     * @returns {Promise<Object<string, string>|null>}
     */
    async fetchHostFiles(studentId) {
      const sid = studentId || getStudentId();
      try {
        const res = await fetch('/api/workspace/files?student_id=' + encodeURIComponent(sid));
        if (!res.ok) return null;
        const data = await res.json();
        if (data && data.ok && data.files) {
          return data.files;
        }
      } catch (err) {
        console.warn('[VLabWorkspace] Failed to fetch host files:', err);
      }
      return null;
    },

    /**
     * Persists workspace files permanently to the host device
     * @param {Object<string, string>} filesMap Dictionary of filename -> content
     * @param {string} [studentId] Optional student ID override
     * @param {boolean} [immediate=false] If true, bypasses debouncing
     */
    saveFilesToHost(filesMap, studentId, immediate = false) {
      const sid = studentId || getStudentId();
      if (!filesMap || typeof filesMap !== 'object') return;

      const triggerSave = async () => {
        try {
          await fetch('/api/workspace/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              student_id: sid,
              files: filesMap
            })
          });
        } catch (err) {
          console.warn('[VLabWorkspace] Error saving files to host:', err);
        }
      };

      if (immediate) {
        if (debounceTimer) clearTimeout(debounceTimer);
        triggerSave();
      } else {
        if (debounceTimer) clearTimeout(debounceTimer);
        debounceTimer = setTimeout(triggerSave, 600);
      }
    },

    /**
     * Permanently deletes a file from the host device
     * @param {string} filename Name of the file to remove
     * @param {string} [studentId] Optional student ID override
     */
    async deleteFileFromHost(filename, studentId) {
      const sid = studentId || getStudentId();
      if (!filename) return;
      try {
        await fetch('/api/workspace/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            student_id: sid,
            filename: filename
          })
        });
      } catch (err) {
        console.warn('[VLabWorkspace] Error deleting file on host:', err);
      }
    },

    /**
     * Renames a file permanently on the host device
     * @param {string} oldFilename Old file name
     * @param {string} newFilename New file name
     * @param {string} [studentId] Optional student ID override
     */
    async renameFileOnHost(oldFilename, newFilename, studentId) {
      const sid = studentId || getStudentId();
      if (!oldFilename || !newFilename) return;
      try {
        await fetch('/api/workspace/rename', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            student_id: sid,
            old_filename: oldFilename,
            new_filename: newFilename
          })
        });
      } catch (err) {
        console.warn('[VLabWorkspace] Error renaming file on host:', err);
      }
    }
  };

  global.VLabWorkspace = VLabWorkspace;
})(typeof window !== 'undefined' ? window : this);
