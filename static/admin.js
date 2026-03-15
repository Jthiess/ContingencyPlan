// Admin panel — loaded only on /admin, which Flask serves exclusively to admins.

const adminState = {
  pollTimers: {},
  selectedUserId: null,
  selectedUserName: null,
  channelAccessGuildId: null,
  defaultChannelGuildId: null,
  discordSearchTimer: null,
  hiddenAuthorSearchTimer: null,
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function api(path) {
  const res = await fetch(path);
  if (res.status === 401 || res.status === 403) {
    window.location.href = '/';
    throw new Error('Not authorized');
  }
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

// ── Init ──────────────────────────────────────────────────────────────────────

function init() {
  // Tab switching
  document.querySelectorAll('.admin-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.admin-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const tab = btn.dataset.tab;
      document.querySelectorAll('.admin-tab-content').forEach(c => c.classList.add('hidden'));
      document.getElementById(`admin-tab-${tab}`).classList.remove('hidden');
      if (tab === 'config') loadAdminConfig();
      else if (tab === 'guilds') loadSavedGuilds();
      else if (tab === 'users') loadAdminUsers();
      else if (tab === 'defaults') loadDefaultPermissions();
      else if (tab === 'scheduler') loadSchedules();
      else if (tab === 'logging') loadLoggingSettings();
    });
  });

  // Config form
  document.getElementById('config-form').addEventListener('submit', async e => {
    e.preventDefault();
    const data = {};
    new FormData(e.target).forEach((v, k) => { data[k] = v; });
    const statusEl = document.getElementById('config-status');
    try {
      const res = await fetch('/api/admin/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error(await res.text());
      statusEl.className = '';
      statusEl.textContent = 'Saved!';
      setTimeout(() => { statusEl.textContent = ''; }, 3000);
    } catch (err) {
      statusEl.className = 'error';
      statusEl.textContent = `Error: ${err.message}`;
    }
  });

  // Clone buttons
  document.getElementById('clone-new-btn').addEventListener('click', () => startCloneFromInput(false));
  document.getElementById('clone-new-full-btn').addEventListener('click', () => startCloneFromInput(true));

  // Scheduler form
  document.getElementById('sched-add-btn').addEventListener('click', async () => {
    const guildId = document.getElementById('sched-guild-id').value.trim();
    const intervalHours = parseInt(document.getElementById('sched-interval').value, 10);
    const full = document.getElementById('sched-full').checked;
    const skipDownloads = document.getElementById('sched-skip-dl').checked;
    if (!guildId) { document.getElementById('sched-guild-id').focus(); return; }
    try {
      const res = await fetch('/api/admin/schedules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ guild_id: guildId, interval_hours: intervalHours, full, skip_downloads: skipDownloads }),
      });
      if (!res.ok) { const d = await res.json(); alert(d.error || 'Failed'); return; }
      document.getElementById('sched-guild-id').value = '';
      loadSchedules();
    } catch (err) {
      alert(`Error: ${err.message}`);
    }
  });

  // Rotate log button
  document.getElementById('rotate-log-btn').addEventListener('click', async () => {
    const statusEl = document.getElementById('rotate-status');
    try {
      await fetch('/api/admin/logging/rotate', { method: 'POST' });
      statusEl.textContent = 'Rotated!';
      setTimeout(() => { statusEl.textContent = ''; loadLoggingSettings(); }, 2000);
    } catch (err) {
      statusEl.textContent = `Error: ${err.message}`;
    }
  });

  // Load default tab
  loadSavedGuilds();
}

document.addEventListener('DOMContentLoaded', init);

// ── Config ────────────────────────────────────────────────────────────────────

async function loadAdminConfig() {
  try {
    const config = await api('/api/admin/config');
    const form = document.getElementById('config-form');
    for (const [key, val] of Object.entries(config)) {
      const input = form.querySelector(`[name="${key}"]`);
      if (input) input.value = val;
    }
  } catch (err) {
    console.error('Failed to load config:', err);
  }
}

// ── Guilds / Clone ────────────────────────────────────────────────────────────

async function loadSavedGuilds() {
  const container = document.getElementById('saved-guilds-list');
  container.innerHTML = '<div class="admin-loading">Loading guilds…</div>';
  try {
    const guilds = await api('/api/admin/guilds');
    if (!guilds.length) {
      container.innerHTML = '<div class="admin-loading">No guilds archived yet.</div>';
      return;
    }
    container.innerHTML = '';
    for (const g of guilds) container.appendChild(buildGuildCard(g));
  } catch (err) {
    container.innerHTML = `<div class="admin-loading" style="color:var(--red)">Failed to load: ${escHtml(err.message)}</div>`;
  }
}

function buildGuildCard(guild) {
  const card = document.createElement('div');
  card.className = 'saved-guild-card';
  card.dataset.guildId = guild.id;

  const iconHtml = guild.icon_hash
    ? `<div class="saved-guild-icon"><img src="/downloads/${guild.id}/guild/icon.png" onerror="this.parentElement.textContent='${escHtml((guild.name||'?')[0])}'" /></div>`
    : `<div class="saved-guild-icon">${escHtml((guild.name||'?')[0])}</div>`;

  card.innerHTML = `
    <div class="saved-guild-card-header">
      ${iconHtml}
      <div class="saved-guild-info">
        <div class="saved-guild-name">${escHtml(guild.name || 'Unknown')}</div>
        <div class="saved-guild-id">${escHtml(guild.id)}</div>
      </div>
    </div>
    <div class="saved-guild-actions">
      <button class="btn-secondary clone-btn" data-guild-id="${escHtml(guild.id)}" data-full="false">Clone</button>
      <button class="btn-danger clone-btn" data-guild-id="${escHtml(guild.id)}" data-full="true">Full Clone</button>
    </div>
    <div class="clone-status hidden" id="clone-status-${escHtml(guild.id)}"></div>
  `;

  card.querySelectorAll('.clone-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const skipDl = document.getElementById('clone-skip-dl')?.checked || false;
      startClone(btn.dataset.guildId, btn.dataset.full === 'true', skipDl);
    });
  });

  return card;
}

async function startCloneFromInput(full) {
  const input = document.getElementById('clone-guild-id-input');
  const guildId = input.value.trim();
  if (!guildId) { input.focus(); return; }
  const skipDl = document.getElementById('clone-skip-dl').checked;
  await startClone(guildId, full, skipDl);
}

async function startClone(guildId, full, skipDownloads) {
  try {
    const res = await fetch('/api/admin/clone', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ guild_id: guildId, full, skip_downloads: skipDownloads }),
    });
    const data = await res.json();
    if (!res.ok) { alert(data.error || 'Failed to start clone'); return; }
    pollCloneStatus(guildId);
    await loadSavedGuilds();
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

function pollCloneStatus(guildId) {
  if (adminState.pollTimers[guildId]) clearInterval(adminState.pollTimers[guildId]);

  async function checkStatus() {
    try {
      const status = await api(`/api/admin/clone/${guildId}/status`);
      renderCloneStatus(guildId, status);
      if (!status.running) {
        clearInterval(adminState.pollTimers[guildId]);
        delete adminState.pollTimers[guildId];
      }
    } catch (e) { /* ignore */ }
  }

  checkStatus();
  adminState.pollTimers[guildId] = setInterval(checkStatus, 1500);
}

function renderCloneStatus(guildId, status) {
  let statusEl = document.getElementById(`clone-status-${guildId}`);
  if (!statusEl) {
    const container = document.getElementById('saved-guilds-list');
    if (container.querySelector('.admin-loading')) container.innerHTML = '';
    const card = document.createElement('div');
    card.className = 'saved-guild-card';
    card.innerHTML = `
      <div class="saved-guild-card-header">
        <div class="saved-guild-icon">?</div>
        <div class="saved-guild-info">
          <div class="saved-guild-name">Guild ${escHtml(guildId)}</div>
          <div class="saved-guild-id">${escHtml(guildId)}</div>
        </div>
      </div>
      <div class="clone-status" id="clone-status-${escHtml(guildId)}"></div>
    `;
    container.prepend(card);
    statusEl = document.getElementById(`clone-status-${guildId}`);
  }

  statusEl.classList.remove('hidden');

  let stateLabel, stateClass;
  if (status.running) {
    stateLabel = 'Running…';
    stateClass = 'clone-status-running';
  } else if (status.error) {
    stateLabel = `Error: ${status.error}`;
    stateClass = 'clone-status-error';
  } else if (status.log && status.log.length) {
    stateLabel = 'Completed';
    stateClass = 'clone-status-done';
  } else {
    stateLabel = 'Idle';
    stateClass = '';
  }

  const log = (status.log || []).slice(-30).join('\n');
  const stopBtn = status.running
    ? `<button class="btn-danger" style="font-size:11px;padding:3px 8px" onclick="stopClone('${escHtml(guildId)}')">Stop</button>`
    : '';

  statusEl.innerHTML = `
    <div class="clone-status-header">
      <span class="${stateClass}">${escHtml(stateLabel)}</span>
      ${stopBtn}
    </div>
    ${log ? `<div class="clone-log">${escHtml(log)}</div>` : ''}
  `;

  const logEl = statusEl.querySelector('.clone-log');
  if (logEl) logEl.scrollTop = logEl.scrollHeight;
}

async function stopClone(guildId) {
  try {
    await fetch(`/api/admin/clone/${guildId}/stop`, { method: 'POST' });
  } catch (e) { /* ignore */ }
}

// ── Users ─────────────────────────────────────────────────────────────────────

async function loadAdminUsers() {
  showUsersListView();
  const container = document.getElementById('users-list');
  container.innerHTML = '<div class="admin-loading">Loading users…</div>';
  try {
    const users = await fetch('/api/admin/users').then(r => r.json());
    container.innerHTML = '';
    if (!users.length) {
      container.innerHTML = '<div class="admin-loading">No users yet.</div>';
      return;
    }
    for (const u of users) {
      const card = document.createElement('div');
      card.className = 'user-card';
      const initials = (u.username || '?')[0].toUpperCase();
      const adminBadge = u.is_admin ? '<span class="user-admin-badge">Admin</span>' : '';
      const lastLogin = u.last_login ? new Date(u.last_login).toLocaleDateString() : 'Never';
      const discordNames = (u.discord_links || []).map(d => d.display_name || d.name).join(', ');
      card.innerHTML = `
        <div class="user-card-avatar">${escHtml(initials)}</div>
        <div class="user-card-info">
          <div class="user-card-name">${escHtml(u.username)} ${adminBadge}</div>
          <div class="user-card-meta">${u.email ? escHtml(u.email) : ''}${discordNames ? ` · ${escHtml(discordNames)}` : ''}</div>
          <div class="user-card-meta">Last login: ${escHtml(lastLogin)}</div>
        </div>
        <button class="btn-secondary user-edit-btn" style="font-size:12px;padding:4px 12px">Manage</button>
      `;
      card.querySelector('.user-edit-btn').addEventListener('click', () => openUserDetail(u));
      container.appendChild(card);
    }
  } catch (err) {
    container.innerHTML = `<div class="admin-loading" style="color:var(--red)">Failed: ${escHtml(err.message)}</div>`;
  }
}

function showUsersListView() {
  document.getElementById('users-list-view').classList.remove('hidden');
  document.getElementById('user-detail-view').classList.add('hidden');
  document.getElementById('user-channel-access-section').classList.add('hidden');
  document.getElementById('user-detail-body').classList.remove('split');
  adminState.channelAccessGuildId = null;
}

async function openUserDetail(user) {
  adminState.selectedUserId = user.id;
  adminState.selectedUserName = user.username;
  document.getElementById('users-list-view').classList.add('hidden');
  document.getElementById('user-detail-view').classList.remove('hidden');

  document.getElementById('user-detail-name').textContent = user.username;
  document.getElementById('user-detail-admin-badge').classList.toggle('hidden', !user.is_admin);

  const toggleBtn = document.getElementById('user-toggle-admin-btn');
  toggleBtn.textContent = user.is_admin ? 'Revoke Admin' : 'Grant Admin';
  toggleBtn.onclick = () => toggleUserAdmin(user.id, !user.is_admin);

  document.getElementById('users-back-btn').onclick = loadAdminUsers;

  await Promise.allSettled([
    renderDiscordLinks(user.id, user.discord_links || []),
    renderGuildAccess(user.id),
    renderHiddenAuthors(user.id),
  ]);
  initDiscordLinkSearch(user.id);
  initHiddenAuthorSearch(user.id);
}

function renderDiscordLinks(userId, links) {
  const container = document.getElementById('user-discord-links-list');
  container.innerHTML = '';
  if (!links.length) {
    container.innerHTML = '<div class="admin-loading" style="font-size:13px">No Discord accounts linked.</div>';
    return;
  }
  for (const link of links) {
    const row = document.createElement('div');
    row.className = 'discord-link-row';
    row.innerHTML = `
      <span class="discord-link-name">${escHtml(link.display_name || link.name)}</span>
      <span class="discord-link-tag">${escHtml(link.name)}</span>
      <button class="btn-danger" style="font-size:11px;padding:2px 8px" data-id="${link.discord_user_id}">Unlink</button>
    `;
    row.querySelector('button').addEventListener('click', async () => {
      await fetch(`/api/admin/users/${userId}/discord-links/${link.discord_user_id}`, { method: 'DELETE' });
      const users = await fetch('/api/admin/users').then(r => r.json());
      const u = users.find(u => u.id === userId);
      if (u) renderDiscordLinks(userId, u.discord_links || []);
    });
    container.appendChild(row);
  }
}

function initDiscordLinkSearch(userId) {
  const input = document.getElementById('discord-link-search-input');
  const results = document.getElementById('discord-link-search-results');
  input.value = '';
  results.classList.add('hidden');
  results.innerHTML = '';

  input.oninput = () => {
    clearTimeout(adminState.discordSearchTimer);
    const q = input.value.trim();
    if (!q) { results.classList.add('hidden'); return; }
    adminState.discordSearchTimer = setTimeout(async () => {
      const users = await fetch(`/api/admin/discord-users/search?q=${encodeURIComponent(q)}`).then(r => r.json());
      results.innerHTML = '';
      if (!users.length) {
        results.innerHTML = '<div class="discord-search-no-results">No users found</div>';
        results.classList.remove('hidden');
        return;
      }
      for (const u of users) {
        const item = document.createElement('div');
        item.className = 'discord-search-item';
        item.textContent = u.display_name ? `${u.display_name} (${u.name})` : u.name;
        item.addEventListener('click', async () => {
          results.classList.add('hidden');
          input.value = '';
          await fetch(`/api/admin/users/${userId}/discord-links`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ discord_user_id: u.id }),
          });
          const allUsers = await fetch('/api/admin/users').then(r => r.json());
          const updated = allUsers.find(x => x.id === userId);
          if (updated) renderDiscordLinks(userId, updated.discord_links || []);
        });
        results.appendChild(item);
      }
      results.classList.remove('hidden');
    }, 300);
  };
}

async function renderGuildAccess(userId) {
  const container = document.getElementById('user-guild-access-list');
  container.innerHTML = '<div class="admin-loading" style="font-size:13px">Loading…</div>';
  const perms = await fetch(`/api/admin/users/${userId}/guild-access`).then(r => r.json());
  container.innerHTML = '';
  if (!perms.length) {
    container.innerHTML = '<div class="admin-loading" style="font-size:13px">No guilds archived.</div>';
    return;
  }
  for (const p of perms) {
    const row = document.createElement('div');
    row.className = 'perm-row';
    const iconEl = p.icon_hash
      ? `<img src="/downloads/${p.guild_id}/guild/icon.png" class="perm-guild-icon" onerror="this.style.display='none'" />`
      : `<div class="perm-guild-icon perm-guild-icon-text">${escHtml((p.name || '?')[0])}</div>`;
    row.innerHTML = `
      ${iconEl}
      <span class="perm-name">${escHtml(p.name)}</span>
      <label class="perm-toggle">
        <input type="checkbox" ${p.can_access ? 'checked' : ''} />
        <span class="perm-toggle-label">${p.can_access ? 'Allowed' : 'Denied'}</span>
      </label>
      <button class="btn-secondary perm-channels-btn" style="font-size:11px;padding:3px 8px" ${!p.can_access ? 'disabled' : ''}>Channels</button>
    `;
    const checkbox = row.querySelector('input[type=checkbox]');
    const label = row.querySelector('.perm-toggle-label');
    const channelsBtn = row.querySelector('.perm-channels-btn');
    checkbox.addEventListener('change', async () => {
      const val = checkbox.checked;
      label.textContent = val ? 'Allowed' : 'Denied';
      channelsBtn.disabled = !val;
      await fetch(`/api/admin/users/${userId}/guild-access`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ guild_id: p.guild_id, can_access: val }),
      });
    });
    channelsBtn.addEventListener('click', () => openChannelAccess(userId, p.guild_id, p.name));
    container.appendChild(row);
  }
}

async function openChannelAccess(userId, guildId, guildName) {
  adminState.channelAccessGuildId = guildId;
  document.getElementById('channel-access-guild-name').textContent = guildName;
  document.getElementById('user-channel-access-section').classList.remove('hidden');
  document.getElementById('user-detail-body').classList.add('split');

  const filterInput = document.getElementById('channel-filter-input');
  filterInput.value = '';

  const container = document.getElementById('user-channel-access-list');
  container.innerHTML = '<div class="admin-loading" style="font-size:13px">Loading…</div>';
  const perms = await fetch(`/api/admin/users/${userId}/channel-access/${guildId}`).then(r => r.json());
  container.innerHTML = '';

  if (!perms.length) {
    container.innerHTML = '<div class="admin-loading" style="font-size:13px">No channels found.</div>';
    return;
  }

  const rowEls = [];
  for (const p of perms) {
    const row = document.createElement('div');
    row.className = 'perm-row perm-row-channel';
    row.dataset.channelName = p.name.toLowerCase();
    const isAllowed = p.can_access !== false;
    const isVoice = p.type === 'voice' || p.type === 'stage';
    const channelIcon = isVoice
      ? `<svg class="perm-channel-hash" width="14" height="14" viewBox="0 0 24 24" fill="currentColor" title="Voice channel"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02z"/></svg>`
      : `<span class="perm-channel-hash">#</span>`;
    row.innerHTML = `
      ${channelIcon}
      <span class="perm-name">${escHtml(p.name)}</span>
      <label class="perm-toggle">
        <input type="checkbox" ${isAllowed ? 'checked' : ''} />
        <span class="perm-toggle-label">${isAllowed ? 'Allowed' : 'Denied'}</span>
      </label>
    `;
    const checkbox = row.querySelector('input[type=checkbox]');
    const label = row.querySelector('.perm-toggle-label');
    checkbox.addEventListener('change', async () => {
      const val = checkbox.checked;
      label.textContent = val ? 'Allowed' : 'Denied';
      await fetch(`/api/admin/users/${userId}/channel-access`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel_id: p.channel_id, can_access: val }),
      });
    });
    container.appendChild(row);
    rowEls.push({ row, p });
  }

  filterInput.oninput = () => {
    const q = filterInput.value.toLowerCase();
    for (const { row } of rowEls) {
      row.style.display = (!q || row.dataset.channelName.includes(q)) ? '' : 'none';
    }
  };

  document.getElementById('allow-all-channels-btn').onclick = async () => {
    for (const { p, row } of rowEls) {
      const cb = row.querySelector('input[type=checkbox]');
      cb.checked = true;
      row.querySelector('.perm-toggle-label').textContent = 'Allowed';
      await fetch(`/api/admin/users/${userId}/channel-access`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel_id: p.channel_id, can_access: true }),
      });
    }
  };

  document.getElementById('deny-all-channels-btn').onclick = async () => {
    for (const { p, row } of rowEls) {
      const cb = row.querySelector('input[type=checkbox]');
      cb.checked = false;
      row.querySelector('.perm-toggle-label').textContent = 'Denied';
      await fetch(`/api/admin/users/${userId}/channel-access`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel_id: p.channel_id, can_access: false }),
      });
    }
  };

  document.getElementById('clear-channel-perms-btn-top').onclick = async () => {
    for (const { p, row } of rowEls) {
      const cb = row.querySelector('input[type=checkbox]');
      cb.checked = true;
      row.querySelector('.perm-toggle-label').textContent = 'Allowed';
      await fetch(`/api/admin/users/${userId}/channel-access`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel_id: p.channel_id, can_access: null }),
      });
    }
  };
}

async function renderHiddenAuthors(userId) {
  const container = document.getElementById('user-hidden-authors-list');
  container.innerHTML = '<div class="admin-loading" style="font-size:13px">Loading…</div>';
  let authors;
  try {
    const res = await fetch(`/api/admin/users/${userId}/hidden-authors`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    authors = await res.json();
  } catch (e) {
    container.innerHTML = `<div class="admin-loading" style="font-size:13px;color:var(--red)">Failed to load: ${escHtml(e.message)}</div>`;
    return;
  }
  container.innerHTML = '';
  if (!authors.length) {
    container.innerHTML = '<div class="admin-loading" style="font-size:13px">No authors hidden.</div>';
    return;
  }
  for (const a of authors) {
    const row = document.createElement('div');
    row.className = 'discord-link-row';
    row.innerHTML = `
      <span class="discord-link-name">${escHtml(a.display_name || a.name)}</span>
      <span class="discord-link-tag">${escHtml(a.name)}</span>
      <button class="btn-danger" style="font-size:11px;padding:2px 8px">Unhide</button>
    `;
    row.querySelector('button').addEventListener('click', async () => {
      await fetch(`/api/admin/users/${userId}/hidden-authors/${a.id}`, { method: 'DELETE' });
      renderHiddenAuthors(userId);
    });
    container.appendChild(row);
  }
}

function initHiddenAuthorSearch(userId) {
  const input = document.getElementById('hidden-author-search-input');
  const results = document.getElementById('hidden-author-search-results');
  input.value = '';
  results.classList.add('hidden');
  results.innerHTML = '';

  input.oninput = () => {
    clearTimeout(adminState.hiddenAuthorSearchTimer);
    const q = input.value.trim();
    if (!q) { results.classList.add('hidden'); return; }
    adminState.hiddenAuthorSearchTimer = setTimeout(async () => {
      try {
        const res = await fetch(`/api/admin/discord-users/search?q=${encodeURIComponent(q)}`);
        const users = res.ok ? await res.json() : [];
        results.innerHTML = '';
        if (!users.length) {
          results.innerHTML = '<div class="discord-search-no-results">No users found</div>';
          results.classList.remove('hidden');
          return;
        }
        for (const u of users) {
          const item = document.createElement('div');
          item.className = 'discord-search-item';
          item.textContent = u.display_name ? `${u.display_name} (${u.name})` : u.name;
          item.addEventListener('click', async () => {
            results.classList.add('hidden');
            input.value = '';
            await fetch(`/api/admin/users/${userId}/hidden-authors`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ discord_user_id: u.id }),
            });
            renderHiddenAuthors(userId);
          });
          results.appendChild(item);
        }
        results.classList.remove('hidden');
      } catch (_) { /* ignore */ }
    }, 300);
  };
}

async function toggleUserAdmin(userId, makeAdmin) {
  await fetch(`/api/admin/users/${userId}/admin`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_admin: makeAdmin }),
  });
  const btn = document.getElementById('user-toggle-admin-btn');
  const badge = document.getElementById('user-detail-admin-badge');
  btn.textContent = makeAdmin ? 'Revoke Admin' : 'Grant Admin';
  btn.onclick = () => toggleUserAdmin(userId, !makeAdmin);
  badge.classList.toggle('hidden', !makeAdmin);
}

// ── Default Permissions ───────────────────────────────────────────────────────

async function loadDefaultPermissions() {
  const container = document.getElementById('default-guild-permissions-list');
  container.innerHTML = '<div class="admin-loading" style="font-size:13px">Loading…</div>';
  // Reset channel panel
  document.getElementById('defaults-channel-section').classList.add('hidden');
  document.getElementById('defaults-body').classList.remove('split');
  adminState.defaultChannelGuildId = null;

  let perms;
  try {
    perms = await api('/api/admin/default-permissions');
  } catch (e) {
    container.innerHTML = `<div class="admin-loading" style="font-size:13px;color:var(--red)">Failed to load: ${escHtml(String(e.message || e))}</div>`;
    return;
  }
  container.innerHTML = '';
  if (!perms.length) {
    container.innerHTML = '<div class="admin-loading" style="font-size:13px">No guilds archived.</div>';
    return;
  }
  for (const p of perms) {
    const row = document.createElement('div');
    row.className = 'perm-row';
    const name = p.name || 'Unknown';
    const iconEl = p.icon_hash
      ? `<img src="/downloads/${p.guild_id}/guild/icon.png" class="perm-guild-icon" onerror="this.style.display='none'" />`
      : `<div class="perm-guild-icon perm-guild-icon-text">${escHtml(name[0])}</div>`;
    row.innerHTML = `
      ${iconEl}
      <span class="perm-name">${escHtml(name)}</span>
      <label class="perm-toggle">
        <input type="checkbox" ${p.can_access ? 'checked' : ''} />
        <span class="perm-toggle-label">${p.can_access ? 'Allowed' : 'Denied'}</span>
      </label>
      <button class="btn-secondary perm-channels-btn" style="font-size:11px;padding:3px 8px">Channels</button>
    `;
    const checkbox = row.querySelector('input[type=checkbox]');
    const label = row.querySelector('.perm-toggle-label');
    const channelsBtn = row.querySelector('.perm-channels-btn');
    checkbox.addEventListener('change', async () => {
      const val = checkbox.checked;
      label.textContent = val ? 'Allowed' : 'Denied';
      await fetch('/api/admin/default-permissions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ guild_id: p.guild_id, can_access: val }),
      });
    });
    channelsBtn.addEventListener('click', () => openDefaultChannelAccess(p.guild_id, name));
    container.appendChild(row);
  }
}

async function openDefaultChannelAccess(guildId, guildName) {
  adminState.defaultChannelGuildId = guildId;
  document.getElementById('default-channel-guild-name').textContent = guildName;
  document.getElementById('defaults-channel-section').classList.remove('hidden');
  document.getElementById('defaults-body').classList.add('split');

  const filterInput = document.getElementById('default-channel-filter-input');
  filterInput.value = '';

  const container = document.getElementById('default-channel-access-list');
  container.innerHTML = '<div class="admin-loading" style="font-size:13px">Loading…</div>';
  const perms = await fetch(`/api/admin/default-permissions/${guildId}/channels`).then(r => r.json());
  container.innerHTML = '';

  if (!perms.length) {
    container.innerHTML = '<div class="admin-loading" style="font-size:13px">No channels found.</div>';
    return;
  }

  const rowEls = [];
  for (const p of perms) {
    const row = document.createElement('div');
    row.className = 'perm-row perm-row-channel';
    row.dataset.channelName = p.name.toLowerCase();
    // null = no override (shown as allowed); true = explicit allow; false = explicit deny
    const isAllowed = p.can_access !== false;
    const hasOverride = p.can_access !== null;
    const isVoice = p.type === 'voice' || p.type === 'stage';
    const channelIcon = isVoice
      ? `<svg class="perm-channel-hash" width="14" height="14" viewBox="0 0 24 24" fill="currentColor" title="Voice channel"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02z"/></svg>`
      : `<span class="perm-channel-hash">#</span>`;
    row.innerHTML = `
      ${channelIcon}
      <span class="perm-name">${escHtml(p.name)}</span>
      <label class="perm-toggle">
        <input type="checkbox" ${isAllowed ? 'checked' : ''} />
        <span class="perm-toggle-label">${hasOverride ? (isAllowed ? 'Allowed' : 'Denied') : 'Default'}</span>
      </label>
    `;
    const checkbox = row.querySelector('input[type=checkbox]');
    const label = row.querySelector('.perm-toggle-label');
    checkbox.addEventListener('change', async () => {
      const val = checkbox.checked;
      label.textContent = val ? 'Allowed' : 'Denied';
      await fetch('/api/admin/default-permissions/channel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel_id: p.channel_id, can_access: val }),
      });
    });
    container.appendChild(row);
    rowEls.push({ row, p });
  }

  filterInput.oninput = () => {
    const q = filterInput.value.toLowerCase();
    for (const { row } of rowEls) {
      row.style.display = (!q || row.dataset.channelName.includes(q)) ? '' : 'none';
    }
  };

  document.getElementById('default-allow-all-btn').onclick = async () => {
    for (const { p, row } of rowEls) {
      const cb = row.querySelector('input[type=checkbox]');
      cb.checked = true;
      row.querySelector('.perm-toggle-label').textContent = 'Allowed';
      await fetch('/api/admin/default-permissions/channel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel_id: p.channel_id, can_access: true }),
      });
    }
  };

  document.getElementById('default-deny-all-btn').onclick = async () => {
    for (const { p, row } of rowEls) {
      const cb = row.querySelector('input[type=checkbox]');
      cb.checked = false;
      row.querySelector('.perm-toggle-label').textContent = 'Denied';
      await fetch('/api/admin/default-permissions/channel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel_id: p.channel_id, can_access: false }),
      });
    }
  };

  document.getElementById('default-clear-all-btn').onclick = async () => {
    for (const { p, row } of rowEls) {
      const cb = row.querySelector('input[type=checkbox]');
      cb.checked = true;
      row.querySelector('.perm-toggle-label').textContent = 'Default';
      await fetch('/api/admin/default-permissions/channel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel_id: p.channel_id, can_access: null }),
      });
    }
  };
}

// ── Scheduler ─────────────────────────────────────────────────────────────────

async function loadSchedules() {
  const container = document.getElementById('schedules-list');
  container.innerHTML = '<div class="admin-loading">Loading…</div>';
  try {
    const schedules = await api('/api/admin/schedules');
    if (!schedules.length) {
      container.innerHTML = '<div class="admin-loading">No schedules configured.</div>';
      return;
    }
    container.innerHTML = '';
    for (const s of schedules) container.appendChild(buildScheduleRow(s));
  } catch (err) {
    container.innerHTML = `<div class="admin-loading" style="color:var(--red)">Failed: ${escHtml(err.message)}</div>`;
  }
}

function buildScheduleRow(s) {
  const row = document.createElement('div');
  row.className = 'perm-row';
  row.style.cssText = 'flex-wrap:wrap;gap:8px;padding:10px 12px';

  const lastRun = s.last_run ? new Date(s.last_run + 'Z').toLocaleString() : 'Never';
  const nextRun = s.next_run ? new Date(s.next_run + 'Z').toLocaleString() : '—';
  const flags = [s.full ? 'Full' : 'Incremental', s.skip_downloads ? 'No DL' : 'With DL'].join(' · ');

  row.innerHTML = `
    <div style="flex:1;min-width:180px">
      <div style="font-size:13px;font-weight:500">Guild ${escHtml(s.guild_id)}</div>
      <div style="font-size:12px;color:var(--muted);margin-top:2px">
        Every ${escHtml(String(s.interval_hours))}h · ${escHtml(flags)}
      </div>
      <div style="font-size:11px;color:var(--muted);margin-top:2px">
        Last: ${escHtml(lastRun)}&ensp;Next: ${escHtml(nextRun)}
      </div>
    </div>
    <div style="display:flex;align-items:center;gap:8px">
      <label class="perm-toggle" style="font-size:12px">
        <input type="checkbox" class="sched-enabled-cb" ${s.enabled ? 'checked' : ''} />
        <span class="perm-toggle-label">${s.enabled ? 'Enabled' : 'Disabled'}</span>
      </label>
      <button class="btn-danger sched-delete-btn" style="font-size:12px;padding:3px 8px">Delete</button>
    </div>
  `;

  const cb = row.querySelector('.sched-enabled-cb');
  const label = row.querySelector('.perm-toggle-label');
  cb.addEventListener('change', async () => {
    label.textContent = cb.checked ? 'Enabled' : 'Disabled';
    await fetch(`/api/admin/schedules/${s.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: cb.checked }),
    });
  });

  row.querySelector('.sched-delete-btn').addEventListener('click', async () => {
    if (!confirm(`Delete schedule for guild ${s.guild_id}?`)) return;
    await fetch(`/api/admin/schedules/${s.id}`, { method: 'DELETE' });
    loadSchedules();
  });

  return row;
}

// ── Logging ───────────────────────────────────────────────────────────────────

async function loadLoggingSettings() {
  try {
    const data = await api('/api/admin/logging');

    const toggle = document.getElementById('debug-logging-toggle');
    toggle.checked = data.debug;
    toggle.onchange = async () => {
      const statusEl = document.getElementById('log-level-status');
      const res = await fetch('/api/admin/logging', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ debug: toggle.checked }),
      });
      if (res.ok) {
        statusEl.textContent = `Set to ${toggle.checked ? 'DEBUG' : 'INFO'}`;
        setTimeout(() => { statusEl.textContent = ''; }, 3000);
      }
    };

    renderLogFileStats(data);
  } catch (err) {
    console.error('Failed to load logging settings:', err);
  }
}

function renderLogFileStats(data) {
  const fmt = b => b < 1024 * 1024 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1024 / 1024).toFixed(2)} MB`;
  const maxMB = (data.max_bytes / 1024 / 1024).toFixed(0);
  let html = `<div style="font-size:13px;margin-bottom:8px">
    Current: <strong>${fmt(data.current_size)}</strong> / ${maxMB} MB max &ensp;·&ensp; ${data.backup_count} backups kept
  </div>`;
  if (data.rotated && data.rotated.length) {
    html += '<div style="font-size:12px;color:var(--muted);margin-bottom:4px">Rotated files:</div>';
    for (const f of data.rotated) {
      html += `<div style="font-size:12px;padding:2px 0">${escHtml(f.name)} — ${fmt(f.size)}</div>`;
    }
  } else {
    html += '<div style="font-size:12px;color:var(--muted)">No rotated files yet.</div>';
  }
  document.getElementById('log-file-stats').innerHTML = html;
}
