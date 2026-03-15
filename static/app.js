// ── State ─────────────────────────────────────────────────────────────────
const state = {
  guilds: [],
  activeGuildId: null,
  channels: [],
  roles: [],
  members: [],
  activeChannelId: null,
  messages: [],
  oldestMessageId: null,
  loadingMessages: false,
  membersVisible: true,
  referencedMessages: {}, // message_id -> message data for reply previews
  currentUser: null,
};

// ── Helpers ────────────────────────────────────────────────────────────────

async function api(path) {
  const res = await fetch(path);
  if (res.status === 401) {
    showLoginOverlay();
    throw new Error('Not authenticated');
  }
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json();
}

function showLoginOverlay() {
  document.getElementById('login-overlay').classList.remove('hidden');
}

function updateUserPanel(user) {
  const link = user.discord_links && user.discord_links[0];
  if (link) {
    document.getElementById('self-name').textContent = link.display_name || link.name;
    document.getElementById('self-tag').textContent = user.username;
  } else {
    document.getElementById('self-name').textContent = user.username;
    document.getElementById('self-tag').textContent = user.is_admin ? 'Admin' : 'Viewer';
  }
  const avatarEl = document.getElementById('self-avatar');
  if (link && link.avatar_hash) {
    let cdnUrl, ext;
    if (link.avatar_hash.startsWith('http')) {
      // Legacy: full CDN URL stored as avatar_hash
      cdnUrl = link.avatar_hash;
      ext = link.avatar_hash.includes('.gif') ? 'gif' : 'png';
    } else {
      ext = link.avatar_hash.startsWith('a_') ? 'gif' : 'png';
      cdnUrl = `https://cdn.discordapp.com/avatars/${link.discord_user_id}/${link.avatar_hash}.${ext}`;
    }
    const img = document.createElement('img');
    img.style.cssText = 'width:100%;height:100%;object-fit:cover;border-radius:50%';
    avatarEl.innerHTML = '';
    avatarEl.appendChild(img);
    if (state.activeGuildId) {
      const localUrl = `/downloads/${state.activeGuildId}/avatars/${link.discord_user_id}.${ext}`;
      img.src = localUrl;
      img.onerror = () => { img.src = cdnUrl; img.onerror = () => { img.remove(); avatarEl.textContent = (user.username || '?')[0].toUpperCase(); }; };
    } else {
      img.src = cdnUrl;
      img.onerror = () => { img.remove(); avatarEl.textContent = (user.username || '?')[0].toUpperCase(); };
    }
  } else {
    avatarEl.textContent = (user.username || '?')[0].toUpperCase();
  }
}

function intToHex(n) {
  if (!n) return null;
  const hex = n.toString(16).padStart(6, '0');
  return `#${hex}`;
}

function avatarUrl(userId, avatarHash) {
  if (!avatarHash) return null;
  const ext = avatarHash.startsWith('a_') ? 'gif' : 'png';
  // Try local first, fall back to CDN
  return `/downloads/${state.activeGuildId}/avatars/${userId}.${ext}`;
}

function guildIconUrl(guildId, iconHash) {
  if (!iconHash) return null;
  const ext = iconHash.startsWith('a_') ? 'gif' : 'png';
  return `/downloads/${guildId}/guild/icon.${ext}`;
}

function makeAvatar(userId, avatarHash, displayName) {
  const localUrl = avatarUrl(userId, avatarHash);
  const letter = (displayName || '?')[0].toUpperCase();
  const color = userColor(userId);
  const div = document.createElement('div');
  div.className = 'msg-avatar';
  div.style.background = color;
  if (avatarHash) {
    let cdnUrl;
    if (avatarHash.startsWith('http')) {
      cdnUrl = avatarHash;
    } else {
      const ext = avatarHash.startsWith('a_') ? 'gif' : 'png';
      cdnUrl = `https://cdn.discordapp.com/avatars/${userId}/${avatarHash}.${ext}`;
    }
    const img = document.createElement('img');
    img.src = localUrl || cdnUrl;
    img.alt = displayName;
    img.onerror = () => {
      if (localUrl && img.src !== cdnUrl) {
        img.src = cdnUrl;
        img.onerror = () => { img.remove(); div.textContent = letter; };
      } else {
        img.remove();
        div.textContent = letter;
      }
    };
    div.appendChild(img);
  } else {
    div.textContent = letter;
  }
  return div;
}

function userColor(userId) {
  const colors = [
    '#5865f2', '#eb459e', '#57f287', '#fee75c', '#ed4245',
    '#1abc9c', '#3498db', '#9b59b6', '#e91e63', '#ff9800',
  ];
  const hash = String(userId).split('').reduce((a, c) => a + c.charCodeAt(0), 0);
  return colors[hash % colors.length];
}

/** Returns the highest hoisted role color for a member, or null */
function memberRoleColor(roleIds) {
  if (!roleIds || !roleIds.length) return null;
  const rolesMap = Object.fromEntries(state.roles.map(r => [String(r.id), r]));
  let top = null;
  for (const rid of roleIds) {
    const r = rolesMap[rid];
    if (r && r.color && r.hoist) {
      if (!top || r.position > top.position) top = r;
    }
  }
  return top ? intToHex(top.color) : null;
}

function formatTimestamp(iso, full = false) {
  const d = new Date(iso);
  if (full) {
    return d.toLocaleString([], { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  }
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
  const wasYesterday = d.toDateString() === yesterday.toDateString();

  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (sameDay) return `Today at ${time}`;
  if (wasYesterday) return `Yesterday at ${time}`;
  return d.toLocaleDateString([], { month: '2-digit', day: '2-digit', year: '2-digit' }) + ' ' + time;
}

function formatTimestampMini(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function guessContentType(filename) {
  const ext = (filename || '').split('.').pop().toLowerCase();
  const map = {
    jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', gif: 'image/gif',
    webp: 'image/webp', bmp: 'image/bmp', svg: 'image/svg+xml', tiff: 'image/tiff',
    mp4: 'video/mp4', webm: 'video/webm', mov: 'video/quicktime', avi: 'video/avi',
    mkv: 'video/x-matroska',
    mp3: 'audio/mpeg', ogg: 'audio/ogg', wav: 'audio/wav', flac: 'audio/flac',
    aac: 'audio/aac', m4a: 'audio/mp4', opus: 'audio/ogg',
  };
  return map[ext] || null;
}

function formatSize(bytes) {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function escapeHtml(str) {
  if (!str) return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Very basic Discord markdown → HTML */
function renderMarkdown(text) {
  if (!text) return '';
  let out = escapeHtml(text);
  // Code blocks (``` ```)
  out = out.replace(/```(?:\w+\n)?([\s\S]*?)```/g, (_, code) => `<pre><code>${code.trim()}</code></pre>`);
  // Inline code
  out = out.replace(/`([^`]+)`/g, (_, code) => `<code>${code}</code>`);
  // Bold + italic
  out = out.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
  // Bold
  out = out.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // Italic
  out = out.replace(/\*(.+?)\*/g, '<em>$1</em>');
  out = out.replace(/_(.+?)_/g, '<em>$1</em>');
  // Strikethrough
  out = out.replace(/~~(.+?)~~/g, '<del>$1</del>');
  // Underline
  out = out.replace(/__(.+?)__/g, '<u>$1</u>');
  // Spoiler
  out = out.replace(/\|\|(.+?)\|\|/g, '<span style="background:var(--text-muted);color:var(--text-muted);border-radius:3px;padding:0 2px;" onclick="this.style.color=\'var(--text-normal)\'">$1</span>');
  // Inline image/GIF URLs (before general URL handling)
  out = out.replace(/(https?:\/\/[^\s<>"]+\.(?:gif|png|jpg|jpeg|webp)(?:\?[^\s<>"]*)?)/gi,
    '<img src="$1" class="inline-image" loading="lazy">');
  // URLs (skip already-converted img src attributes)
  out = out.replace(/(?<!src=")(https?:\/\/[^\s<>"]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  // Block quotes
  out = out.replace(/^&gt; (.+)$/gm, '<div style="border-left:3px solid var(--interactive-muted);padding-left:10px;margin:2px 0;color:var(--text-muted)">$1</div>');
  // Mentions @user, #channel
  out = out.replace(/&lt;@!?(\d+)&gt;/g, (_, id) => {
    const member = state.members.find(m => String(m.id) === id);
    const name = member ? (member.nickname || member.display_name || member.name) : id;
    return `<span style="color:var(--brand);background:rgba(88,101,242,.1);border-radius:3px;padding:0 2px">@${escapeHtml(name)}</span>`;
  });
  out = out.replace(/&lt;#(\d+)&gt;/g, (_, id) => {
    const ch = state.channels.find(c => String(c.id) === id);
    const name = ch ? ch.name : id;
    return `<span style="color:var(--brand);background:rgba(88,101,242,.1);border-radius:3px;padding:0 2px">#${escapeHtml(name)}</span>`;
  });
  // Discord lists
  out = out.replace(/^\s*\* (.+)$/gm, '<li>$1</li>');
  out = out.replace(/(<li>.*<\/li>)/g, '<ul>$1</ul>');
  // Discord emoji :emoji:
  out = out.replace(/:([a-zA-Z0-9_]+):/g, (_, name) => {
    // Try to render custom emoji if available
    // Otherwise fallback to unicode
    return `<span class="discord-emoji">:${name}:</span>`;
  });
  // Discord ordered lists
  out = out.replace(/^\s*\d+\. (.+)$/gm, '<li>$1</li>');
  out = out.replace(/(<li>.*<\/li>)/g, '<ol>$1</ol>');
  // Discord horizontal rule
  out = out.replace(/^---$/gm, '<hr>');
  return out;
}

// ── Lightbox ───────────────────────────────────────────────────────────────

function openLightbox(src) {
  const existing = document.getElementById('lightbox');
  if (existing) existing.remove();
  const lb = document.createElement('div');
  lb.id = 'lightbox';
  const img = document.createElement('img');
  img.src = src;
  lb.appendChild(img);
  lb.addEventListener('click', () => lb.remove());
  document.body.appendChild(lb);
}

// ── Render: Server List ────────────────────────────────────────────────────

function renderGuildList() {
  const container = document.getElementById('guild-icons');
  container.innerHTML = '';
  for (const g of state.guilds) {
    const el = document.createElement('div');
    el.className = 'server-icon';
    el.title = g.name;
    el.dataset.id = g.id;
    if (String(g.id) === String(state.activeGuildId)) el.classList.add('active');

    const iconUrl = guildIconUrl(g.id, g.icon_hash);
    if (iconUrl) {
      const img = document.createElement('img');
      img.src = iconUrl;
      img.alt = g.name;
      img.onerror = () => { img.remove(); el.textContent = g.name.slice(0, 2).toUpperCase(); };
      el.appendChild(img);
    } else {
      el.textContent = g.name.slice(0, 2).toUpperCase();
    }

    el.addEventListener('click', () => selectGuild(g.id));
    container.appendChild(el);
  }
}

// ── Render: Channel List ───────────────────────────────────────────────────

function renderChannelList() {
  const list = document.getElementById('channel-list');
  list.innerHTML = '';

  const allThreads = state.channels.filter(c => isThread(c.type));

  const categories = state.channels.filter(c => c.type === 'category').sort((a, b) => a.position - b.position);
  const noCategory = state.channels
    .filter(c => c.type !== 'category' && !isThread(c.type) && !c.category_id)
    .sort((a, b) => a.position - b.position);

  // Channels without a category first
  for (const ch of noCategory) {
    list.appendChild(makeChannelItem(ch));
  }

  // Then categories with their children
  for (const cat of categories) {
    const catEl = document.createElement('div');
    catEl.className = 'channel-category';

    const header = document.createElement('div');
    header.className = 'category-header';
    header.innerHTML = `
      <svg class="category-arrow" viewBox="0 0 24 24" fill="currentColor"><path d="M16.59 8.59L12 13.17 7.41 8.59 6 10l6 6 6-6z"/></svg>
      <span class="category-name">${escapeHtml(cat.name)}</span>
    `;
    header.addEventListener('click', () => {
      header.classList.toggle('collapsed');
      children.classList.toggle('hidden');
    });
    catEl.appendChild(header);

    const children = document.createElement('div');
    children.className = 'category-children';
    const kids = state.channels
      .filter(c => String(c.category_id) === String(cat.id) && c.type !== 'category' && !isThread(c.type))
      .sort((a, b) => a.position - b.position);
    for (const ch of kids) {
      children.appendChild(makeChannelItem(ch));
    }
    catEl.appendChild(children);
    list.appendChild(catEl);
  }

  // All threads as a single separate dropdown at the bottom
  if (allThreads.length) {
    list.appendChild(makeThreadGroup(null, allThreads));
  }
}

function makeThreadGroup(_parentId, threads) {
  const wrap = document.createElement('div');
  wrap.className = 'thread-group';

  const header = document.createElement('div');
  header.className = 'thread-group-header';
  header.innerHTML = `
    <svg class="thread-group-arrow" viewBox="0 0 24 24" fill="currentColor"><path d="M16.59 8.59L12 13.17 7.41 8.59 6 10l6 6 6-6z"/></svg>
    <span>Threads — ${threads.length}</span>
  `;

  const threadList = document.createElement('div');
  threadList.className = 'thread-group-list';
  for (const t of threads.sort((a, b) => String(a.name).localeCompare(String(b.name)))) {
    threadList.appendChild(makeChannelItem(t));
  }

  header.addEventListener('click', () => {
    header.classList.toggle('collapsed');
    threadList.classList.toggle('hidden');
  });

  wrap.appendChild(header);
  wrap.appendChild(threadList);
  return wrap;
}

function isThread(type) {
  return type === 'public_thread' || type === 'private_thread' || type === 'news_thread';
}

function channelIcon(type) {
  switch (type) {
    case 'voice': return `<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M11.383 3.07904C11.009 2.92504 10.579 3.01004 10.293 3.29604L6 8.00204H3C2.45 8.00204 2 8.45204 2 9.00204V15.002C2 15.552 2.45 16.002 3 16.002H6L10.293 20.71C10.579 20.996 11.009 21.082 11.383 20.927C11.757 20.772 12 20.407 12 20.002V4.00204C12 3.59804 11.757 3.23304 11.383 3.07904ZM14 5.00195V7.00195C16.757 7.00195 19 9.24295 19 12.002C19 14.761 16.757 17.002 14 17.002V19.002C17.86 19.002 21 15.862 21 12.002C21 8.14195 17.86 5.00195 14 5.00195ZM14 9.00195V11.002C14.552 11.002 15 11.45 15 12.002C15 12.554 14.552 13.002 14 13.002V15.002C15.657 15.002 17 13.659 17 12.002C17 10.345 15.657 9.00195 14 9.00195Z"/></svg>`;
    case 'forum': return `<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M2 5C2 3.897 2.897 3 4 3H20C21.103 3 22 3.897 22 5V15C22 16.103 21.103 17 20 17H7L2 22V5Z"/></svg>`;
    case 'stage': return `<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M19 2H5C3.897 2 3 2.897 3 4V18C3 19.103 3.897 20 5 20H19C20.103 20 21 19.103 21 18V4C21 2.897 20.103 2 19 2ZM12 17C10.346 17 9 15.654 9 14C9 12.346 10.346 11 12 11C13.654 11 15 12.346 15 14C15 15.654 13.654 17 12 17ZM17 9H7V7H17V9Z"/></svg>`;
    case 'public_thread':
    case 'private_thread':
    case 'news_thread':
      return `<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.486 2 2 6.486 2 12C2 17.514 6.486 22 12 22C14.193 22 16.384 21.311 18.217 20L22 22L20.649 17.688C21.527 16.076 22 14.066 22 12C22 6.486 17.514 2 12 2ZM8 13H6V11H8V13ZM13 13H11V11H13V13ZM18 13H16V11H18V13Z"/></svg>`;
    default: return `<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M5.88657 21C5.57547 21 5.3399 20.7189 5.39427 20.4126L6.00001 17H2.59511C2.28449 17 2.04905 16.7198 2.10259 16.4138L2.27759 15.4138C2.31946 15.1818 2.52196 15 2.75001 15H6.35001L7.41001 9H4.00511C3.69449 9 3.45905 8.71977 3.51259 8.41381L3.68759 7.41381C3.72946 7.18182 3.93196 7 4.16001 7H7.76001L8.39677 3.41262C8.43914 3.18091 8.64182 3 8.87001 3H9.87001C10.1811 3 10.4167 3.28107 10.3623 3.58738L9.76001 7H15.76L16.3968 3.41262C16.4391 3.18091 16.6418 3 16.87 3H17.87C18.1811 3 18.4167 3.28107 18.3623 3.58738L17.76 7H21.1649C21.4755 7 21.711 7.28023 21.6574 7.58619L21.4824 8.58619C21.4406 8.81818 21.2381 9 21.0100 9H17.41L16.35 15H19.7549C20.0655 15 20.301 15.2802 20.2474 15.5862L20.0724 16.5862C20.0306 16.8182 19.8281 17 19.6 17H16L15.3632 20.5874C15.3209 20.8191 15.1182 21 14.89 21H13.89C13.5789 21 13.3433 20.7189 13.3977 20.4126L14 17H8.00001L7.36325 20.5874C7.32088 20.8191 7.1182 21 6.89001 21H5.88657ZM9.41001 9L8.35001 15H14.35L15.41 9H9.41001Z"/></svg>`;
  }
}

function makeChannelItem(ch) {
  const el = document.createElement('div');
  el.className = 'channel-item';
  el.dataset.id = ch.id;
  if (String(ch.id) === String(state.activeChannelId)) el.classList.add('active');

  el.innerHTML = `
    <span class="channel-icon">${channelIcon(ch.type)}</span>
    <span class="channel-name">${escapeHtml(ch.name)}</span>
    ${ch.nsfw ? '<span class="channel-nsfw-badge">NSFW</span>' : ''}
  `;

  el.addEventListener('click', () => selectChannel(ch));
  return el;
}

// ── Render: Members List ───────────────────────────────────────────────────

function renderMemberList() {
  const list = document.getElementById('members-list');
  list.innerHTML = '';

  if (!state.members.length) {
    list.innerHTML = '<div class="empty-state">No members found</div>';
    return;
  }

  // Separate hoisted roles
  const hoistedRoles = state.roles.filter(r => r.hoist).sort((a, b) => b.position - a.position);

  // For each hoisted role, collect members
  const grouped = new Map(); // role_id -> members[]
  const ungrouped = [];

  for (const m of state.members) {
    let placed = false;
    for (const hr of hoistedRoles) {
      if ((m.role_ids || []).includes(String(hr.id))) {
        if (!grouped.has(hr.id)) grouped.set(hr.id, []);
        grouped.get(hr.id).push(m);
        placed = true;
        break;
      }
    }
    if (!placed) ungrouped.push(m);
  }

  function renderMember(m) {
    const div = document.createElement('div');
    div.className = 'member-item';

    const displayName = m.nickname || m.display_name || m.name;
    const color = memberRoleColor(m.role_ids) || null;
    const avatarEl = makeMemberAvatar(m);

    div.innerHTML = `
      <div class="member-avatar-wrap"></div>
      <div class="member-info">
        <span class="member-name" style="${color ? `color:${color}` : ''}">${escapeHtml(displayName)}${m.bot ? '<span class="member-bot-badge">BOT</span>' : ''}</span>
      </div>
    `;
    div.querySelector('.member-avatar-wrap').appendChild(avatarEl);
    return div;
  }

  for (const hr of hoistedRoles) {
    const members = grouped.get(hr.id);
    if (!members || !members.length) continue;

    const header = document.createElement('div');
    header.className = 'members-role-header';
    const color = intToHex(hr.color);
    header.innerHTML = `<span style="${color ? `color:${color}` : ''}">${escapeHtml(hr.name)}</span> — ${members.length}`;
    list.appendChild(header);

    for (const m of members) list.appendChild(renderMember(m));
  }

  if (ungrouped.length) {
    const header = document.createElement('div');
    header.className = 'members-role-header';
    header.textContent = `Online — ${ungrouped.length}`;
    list.appendChild(header);
    for (const m of ungrouped) list.appendChild(renderMember(m));
  }
}

function makeMemberAvatar(m) {
  const wrap = document.createElement('div');
  wrap.className = 'member-avatar-wrap';
  const displayName = m.nickname || m.display_name || m.name;
  const av = document.createElement('div');
  av.className = 'member-avatar';
  av.style.background = userColor(m.id);

  const url = avatarUrl(m.id, m.avatar_hash);
  if (url) {
    const img = document.createElement('img');
    img.src = url;
    img.alt = displayName;
    img.onerror = () => { img.remove(); av.textContent = displayName[0].toUpperCase(); };
    av.appendChild(img);
  } else {
    av.textContent = displayName[0].toUpperCase();
  }

  const dot = document.createElement('div');
  dot.className = 'member-status-dot';
  wrap.appendChild(av);
  wrap.appendChild(dot);
  return wrap;
}

// ── Render: Messages ───────────────────────────────────────────────────────

function renderMessages(msgs, prepend = false) {
  const list = document.getElementById('messages-list');
  const placeholder = document.getElementById('messages-placeholder');
  placeholder.classList.add('hidden');

  const fragment = document.createDocumentFragment();
  let lastAuthorId = null;
  let lastMsgTime = null;

  // Group consecutive messages from the same author within 7 minutes
  const THRESHOLD = 7 * 60 * 1000;

  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i];
    const prevMsg = i > 0 ? msgs[i - 1] : null;

    // Date divider
    const mDate = new Date(m.created_at);
    const prevDate = prevMsg ? new Date(prevMsg.created_at) : null;
    if (!prevDate || mDate.toDateString() !== prevDate.toDateString()) {
      const divider = document.createElement('div');
      divider.className = 'message-divider';
      divider.textContent = mDate.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
      fragment.appendChild(divider);
      lastAuthorId = null; // force header after divider
    }

    const timeDiff = lastMsgTime ? mDate - lastMsgTime : Infinity;
    const grouped = m.author_id && m.author_id === lastAuthorId && timeDiff < THRESHOLD && !m.reference_id;

    const group = document.createElement('div');
    group.className = grouped ? 'message-group grouped' : 'message-group';
    group.dataset.id = m.id;

    if (!grouped) {
      // Full header row
      const avatarCol = document.createElement('div');
      avatarCol.className = 'message-avatar-col';
      avatarCol.appendChild(makeAvatar(m.author_id, m.author_avatar, m.author_display || m.author_name));
      group.appendChild(avatarCol);

      const contentCol = document.createElement('div');
      contentCol.className = 'message-content-col';

      // Reply reference
      if (m.reference_id) {
        contentCol.appendChild(makeReplyBar(m));
      }

      const header = document.createElement('div');
      header.className = 'message-header';
      const authorEl = document.createElement('span');
      authorEl.className = 'message-author' + (m.author_bot ? ' bot-tag' : '');
      const displayName = m.author_display || m.author_name || 'Unknown';
      const roleColor = memberRoleColor(getMemberRoleIds(m.author_id));
      authorEl.textContent = displayName;
      if (roleColor) authorEl.style.color = roleColor;

      const tsEl = document.createElement('span');
      tsEl.className = 'message-timestamp';
      tsEl.title = formatTimestamp(m.created_at, true);
      tsEl.textContent = formatTimestamp(m.created_at);

      header.appendChild(authorEl);
      header.appendChild(tsEl);
      contentCol.appendChild(header);

      contentCol.appendChild(makeMessageBody(m));
      group.appendChild(contentCol);
    } else {
      // Grouped — just show time mini on hover
      const avatarCol = document.createElement('div');
      avatarCol.className = 'message-avatar-col';
      const mini = document.createElement('span');
      mini.className = 'message-group-timestamp-mini';
      mini.title = formatTimestamp(m.created_at, true);
      mini.textContent = formatTimestampMini(m.created_at);
      avatarCol.appendChild(mini);
      group.appendChild(avatarCol);

      const contentCol = document.createElement('div');
      contentCol.className = 'message-content-col';
      if (m.reference_id) contentCol.appendChild(makeReplyBar(m));
      contentCol.appendChild(makeMessageBody(m));
      group.appendChild(contentCol);
    }

    fragment.appendChild(group);
    lastAuthorId = m.author_id;
    lastMsgTime = mDate;
  }

  if (prepend) {
    list.insertBefore(fragment, list.firstChild);
  } else {
    list.appendChild(fragment);
  }
}

function makeMessageBody(m) {
  const body = document.createElement('div');

  // Content
  if (m.content) {
    const content = document.createElement('div');
    content.className = 'message-content';
    content.innerHTML = renderMarkdown(m.content);
    content.querySelectorAll('img.inline-image').forEach(img => {
      img.addEventListener('click', () => openLightbox(img.src));
    });
    if (m.edited_at) {
      const edited = document.createElement('span');
      edited.className = 'message-edited';
      edited.textContent = '(edited)';
      edited.title = formatTimestamp(m.edited_at, true);
      content.appendChild(edited);
    }
    body.appendChild(content);
  }

  // Attachments
  if (m.attachments && m.attachments.length) {
    const attEl = document.createElement('div');
    attEl.className = 'message-attachments';
    for (const att of m.attachments) {
      const localUrl = `/downloads/${state.activeGuildId}/attachments/${m.channel_id}/${att.id}_${att.filename}`;
      const contentType = att.content_type || guessContentType(att.filename);
      if (contentType && contentType.startsWith('image/')) {
        const img = document.createElement('img');
        img.className = 'attachment-image';
        img.src = localUrl;
        img.alt = att.filename;
        if (att.width && att.height) {
          // Scale down to max bounds while preserving aspect ratio
          const scale = Math.min(1, 400 / att.width, 300 / att.height);
          img.style.width = Math.round(att.width * scale) + 'px';
          img.style.height = Math.round(att.height * scale) + 'px';
        }
        img.onerror = () => {
          if (att.proxy_url) img.src = att.proxy_url;
          else if (att.url) img.src = att.url;
          else img.style.display = 'none';
        };
        img.addEventListener('click', () => openLightbox(img.src));
        attEl.appendChild(img);
      } else if (contentType && contentType.startsWith('video/')) {
        const video = document.createElement('video');
        video.className = 'attachment-video';
        video.src = localUrl;
        video.controls = true;
        video.onerror = () => {
          if (att.proxy_url) video.src = att.proxy_url;
          else if (att.url) video.src = att.url;
          else video.style.display = 'none';
        };
        attEl.appendChild(video);
      } else if (contentType && contentType.startsWith('audio/')) {
        const audio = document.createElement('audio');
        audio.className = 'attachment-audio';
        audio.src = localUrl;
        audio.controls = true;
        audio.onerror = () => {
          if (att.proxy_url) audio.src = att.proxy_url;
          else if (att.url) audio.src = att.url;
          else audio.style.display = 'none';
        };
        attEl.appendChild(audio);
      } else {
        const file = document.createElement('div');
        file.className = 'attachment-file';
        file.innerHTML = `
          <span class="attachment-file-icon">📎</span>
          <div class="attachment-file-info">
            <a class="attachment-file-name" href="${localUrl}" download="${escapeHtml(att.filename)}" title="${escapeHtml(att.filename)}">${escapeHtml(att.filename)}</a>
            <div class="attachment-file-size">${formatSize(att.size)}</div>
          </div>
          <a class="attachment-file-download" href="${localUrl}" download="${escapeHtml(att.filename)}" title="Download">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>
          </a>
        `;
        attEl.appendChild(file);
      }
    }
    body.appendChild(attEl);
  }

  // Embeds
  if (m.embeds && m.embeds.length) {
    const embedsEl = document.createElement('div');
    embedsEl.className = 'message-embeds';
    for (const emb of m.embeds) {
      embedsEl.appendChild(makeEmbed(emb));
    }
    body.appendChild(embedsEl);
  }

  // Reactions
  if (m.reactions && m.reactions.length) {
    const reactEl = document.createElement('div');
    reactEl.className = 'message-reactions';
    for (const r of m.reactions) {
      const reac = document.createElement('div');
      reac.className = 'reaction';
      const emoji = r.emoji_id
        ? `<img src="https://cdn.discordapp.com/emojis/${r.emoji_id}.png" width="16" height="16" style="vertical-align:middle">`
        : r.emoji_name;
      reac.innerHTML = `<span>${emoji}</span><span class="reaction-count">${r.count}</span>`;
      reactEl.appendChild(reac);
    }
    body.appendChild(reactEl);
  }

  return body;
}

function makeEmbed(emb) {
  const el = document.createElement('div');
  el.className = 'embed';
  const color = intToHex(emb.color) || 'var(--brand)';
  el.style.borderLeftColor = color;

  let html = '<div class="embed-content">';
  // Rich link preview for YouTube
  if (emb.url && /youtube\.com|youtu\.be/.test(emb.url)) {
    const ytIdMatch = emb.url.match(/(?:v=|youtu\.be\/)([\w-]+)/);
    if (ytIdMatch) {
      const ytId = ytIdMatch[1];
      html += `<div class="embed-youtube">
        <iframe width="480" height="270" src="https://www.youtube.com/embed/${ytId}" frameborder="0" allowfullscreen></iframe>
      </div>`;
    }
  }
  // Rich link preview for Twitter
  else if (emb.url && /twitter\.com/.test(emb.url)) {
    html += `<div class="embed-twitter">
      <blockquote class="twitter-tweet"><a href="${escapeHtml(emb.url)}" target="_blank">View Tweet</a></blockquote>
    </div>`;
  }
  // General rich link preview
  else if (emb.url && emb.title && emb.description) {
    html += `<div class="embed-link-card">
      <div class="embed-link-header">
        <a href="${escapeHtml(emb.url)}" target="_blank" class="embed-link-title">${escapeHtml(emb.title)}</a>
      </div>
      <div class="embed-link-desc">${renderMarkdown(emb.description)}</div>
      ${emb.thumbnail_url ? `<img class="embed-link-thumb" src="${escapeHtml(emb.thumbnail_url)}" loading="lazy">` : ''}
    </div>`;
  }
  // Fallback: basic embed rendering
  else {
    if (emb.author_name) html += `<div class="embed-author">${escapeHtml(emb.author_name)}</div>`;
    if (emb.title) {
      html += emb.url
        ? `<div class="embed-title"><a href="${escapeHtml(emb.url)}" target="_blank">${escapeHtml(emb.title)}</a></div>`
        : `<div class="embed-title">${escapeHtml(emb.title)}</div>`;
    }
    if (emb.description) html += `<div class="embed-description">${renderMarkdown(emb.description)}</div>`;
    if (emb.image_url) html += `<div class="embed-image"><img src="${escapeHtml(emb.image_url)}" loading="lazy"></div>`;
    if (emb.video_url) html += `<div class="embed-video"><video src="${escapeHtml(emb.video_url)}" controls style="max-width:100%;max-height:200px;border-radius:4px;"></video></div>`;
    if (emb.audio_url) html += `<div class="embed-audio"><audio src="${escapeHtml(emb.audio_url)}" controls style="width:100%;"></audio></div>`;
    if (emb.footer_text) html += `<div class="embed-footer">${escapeHtml(emb.footer_text)}</div>`;
  }
  html += '</div>';

  if (emb.thumbnail_url) {
    const thumbIsGif = /\.gif(\?.*)?$/i.test(emb.thumbnail_url);
    if (thumbIsGif) {
      html += `<div class="embed-image"><img src="${escapeHtml(emb.thumbnail_url)}" loading="lazy"></div>`;
    } else {
      html += `<div class="embed-thumbnail"><img src="${escapeHtml(emb.thumbnail_url)}" loading="lazy"></div>`;
    }
  }

  el.innerHTML = html;
  return el;
}

function makeReplyBar(m) {
  const div = document.createElement('div');
  div.className = 'message-reply';

  const ref = state.referencedMessages[m.reference_id];
  const authorName = ref ? (ref.author_display || ref.author_name || 'Unknown') : 'Unknown';
  const preview = ref ? (escapeHtml(ref.content || '').slice(0, 80) || '[attachment]') : '[original message]';

  const avatarEl = document.createElement('div');
  avatarEl.className = 'reply-avatar';
  if (ref) {
    const url = avatarUrl(ref.author_id, ref.author_avatar);
    if (url) {
      const img = document.createElement('img');
      img.src = url;
      img.alt = authorName;
      img.onerror = () => { img.remove(); avatarEl.textContent = authorName[0].toUpperCase(); };
      avatarEl.appendChild(img);
    } else {
      avatarEl.textContent = authorName[0].toUpperCase();
      avatarEl.style.background = userColor(ref.author_id);
    }
  }
  div.appendChild(avatarEl);

  const authorSpan = document.createElement('span');
  authorSpan.className = 'reply-author';
  authorSpan.textContent = authorName;
  div.appendChild(authorSpan);

  const previewSpan = document.createElement('span');
  previewSpan.style.cssText = 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
  previewSpan.innerHTML = preview;
  div.appendChild(previewSpan);

  return div;
}

function getMemberRoleIds(userId) {
  const m = state.members.find(m => String(m.id) === String(userId));
  return m ? m.role_ids : [];
}

// ── Actions ────────────────────────────────────────────────────────────────

async function selectGuild(guildId) {
  state.activeGuildId = guildId;
  state.activeChannelId = null;
  state.messages = [];
  state.oldestMessageId = null;

  // Update UI
  renderGuildList();
  document.getElementById('messages-list').innerHTML = '';
  document.getElementById('messages-placeholder').classList.remove('hidden');
  document.getElementById('channel-header-name').textContent = 'Select a channel';
  document.getElementById('channel-header-topic').textContent = '';
  document.getElementById('load-more-btn').classList.add('hidden');

  const guild = state.guilds.find(g => String(g.id) === String(guildId));
  document.getElementById('server-name').textContent = guild ? guild.name : 'Server';

  // Load channels, roles, members
  const [channels, roles, members] = await Promise.all([
    api(`/api/guilds/${guildId}/channels`),
    api(`/api/guilds/${guildId}/roles`),
    api(`/api/guilds/${guildId}/members`),
  ]);

  state.channels = channels;
  state.roles = roles;
  state.members = members;

  renderChannelList();
  renderMemberList();

  // Refresh self-avatar now that we have a guild to load the local file from
  if (state.currentUser) updateUserPanel(state.currentUser);
}

async function selectChannel(ch) {
  if (String(ch.id) === String(state.activeChannelId)) return;

  state.activeChannelId = ch.id;
  state.messages = [];
  state.oldestMessageId = null;
  state.referencedMessages = {};

  // Update active state in sidebar
  document.querySelectorAll('.channel-item').forEach(el => {
    el.classList.toggle('active', el.dataset.id === String(ch.id));
  });

  // Update header
  const isVoice = ch.type === 'voice' || ch.type === 'stage';
  document.getElementById('channel-type-icon').innerHTML = channelIcon(ch.type);
  document.getElementById('channel-header-name').textContent = ch.name;
  document.getElementById('channel-header-topic').textContent = ch.topic || (isVoice ? 'Voice channel text chat' : '');
  document.getElementById('message-bar-placeholder').textContent = `Message #${ch.name}`;

  // Clear messages
  const msgsList = document.getElementById('messages-list');
  msgsList.innerHTML = '';
  document.getElementById('messages-placeholder').classList.add('hidden');
  document.getElementById('load-more-btn').classList.add('hidden');

  // Show spinner
  const spinner = document.createElement('div');
  spinner.className = 'spinner';
  spinner.id = 'msg-spinner';
  msgsList.appendChild(spinner);

  await loadMessages();

  // Scroll to bottom
  const container = document.getElementById('messages-container');
  container.scrollTop = container.scrollHeight;
}

async function loadMessages(prepend = false) {
  if (state.loadingMessages) return;
  state.loadingMessages = true;

  try {
    const params = new URLSearchParams({ limit: 50 });
    if (prepend && state.oldestMessageId) params.set('before', state.oldestMessageId);

    const msgs = await api(`/api/channels/${state.activeChannelId}/messages?${params}`);

    // Remove spinner if first load
    const spinner = document.getElementById('msg-spinner');
    if (spinner) spinner.remove();

    if (!msgs.length) {
      document.getElementById('load-more-btn').classList.add('hidden');
      if (!prepend) {
        // Show channel start
        const msgsList = document.getElementById('messages-list');
        const ch = state.channels.find(c => String(c.id) === String(state.activeChannelId));
        const start = document.createElement('div');
        start.className = 'channel-start-header';
        start.innerHTML = `
          <div class="channel-start-icon">#</div>
          <div class="channel-start-name">${escapeHtml(ch ? ch.name : 'channel')}</div>
          <div class="channel-start-desc">This is the beginning of #${escapeHtml(ch ? ch.name : 'channel')}.</div>
        `;
        msgsList.prepend(start);
      }
      return;
    }

    if (prepend) {
      state.oldestMessageId = msgs[0].id;
    } else {
      state.messages = msgs;
      state.oldestMessageId = msgs[0].id;
    }

    // Pre-fetch referenced messages for reply bars
    const refIds = [...new Set(
      msgs.filter(msg => msg.reference_id && !state.referencedMessages[msg.reference_id])
          .map(msg => msg.reference_id)
    )];
    if (refIds.length) {
      const fetched = await Promise.allSettled(
        refIds.map(id => api(`/api/messages/${id}`).catch(() => null))
      );
      fetched.forEach((result, i) => {
        if (result.status === 'fulfilled' && result.value) {
          state.referencedMessages[refIds[i]] = result.value;
        }
      });
    }

    renderMessages(msgs, prepend);

    // Show load more if we got a full page
    document.getElementById('load-more-btn').classList.toggle('hidden', msgs.length < 50);
  } finally {
    state.loadingMessages = false;
  }
}

// ── Search ─────────────────────────────────────────────────────────────────

let searchDebounce = null;

function initSearch() {
  const btn = document.getElementById('search-btn');
  const bar = document.getElementById('search-bar');
  const input = document.getElementById('search-input');
  const closeBtn = document.getElementById('search-close');
  const results = document.getElementById('search-results');

  btn.addEventListener('click', () => {
    bar.classList.toggle('hidden');
    if (!bar.classList.contains('hidden')) input.focus();
    else { results.classList.add('hidden'); results.innerHTML = ''; }
  });

  closeBtn.addEventListener('click', () => {
    bar.classList.add('hidden');
    results.classList.add('hidden');
    results.innerHTML = '';
    input.value = '';
  });

  input.addEventListener('input', () => {
    clearTimeout(searchDebounce);
    const q = input.value.trim();
    if (!q || !state.activeGuildId) { results.classList.add('hidden'); return; }
    searchDebounce = setTimeout(() => doSearch(q), 300);
  });
}

async function doSearch(q) {
  const results = document.getElementById('search-results');
  results.innerHTML = '<div class="spinner"></div>';
  results.classList.remove('hidden');

  const hits = await api(`/api/guilds/${state.activeGuildId}/search?q=${encodeURIComponent(q)}`);

  results.innerHTML = '';
  if (!hits.length) {
    results.innerHTML = '<div class="empty-state">No results found</div>';
    return;
  }

  const highlight = (text) => {
    if (!text) return '';
    return escapeHtml(text).replace(
      new RegExp(escapeHtml(q).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'),
      m => `<mark>${m}</mark>`
    );
  };

  for (const hit of hits) {
    const div = document.createElement('div');
    div.className = 'search-result-item';
    div.innerHTML = `
      <div class="search-result-channel">#${escapeHtml(hit.channel_name || 'unknown')}</div>
      <div class="search-result-content">${highlight(hit.content)}</div>
    `;
    div.addEventListener('click', async () => {
      // Navigate to the channel
      const ch = state.channels.find(c => String(c.id) === String(hit.channel_id));
      if (ch) await selectChannel(ch);
      document.getElementById('search-bar').classList.add('hidden');
      document.getElementById('search-results').classList.add('hidden');
    });
    results.appendChild(div);
  }
}

// ── Init ───────────────────────────────────────────────────────────────────

async function init() {
  // Check authentication first
  let me;
  try {
    const res = await fetch('/api/me');
    if (res.status === 401) {
      showLoginOverlay();
      return;
    }
    me = await res.json();
  } catch (e) {
    showLoginOverlay();
    return;
  }

  state.currentUser = me;
  updateUserPanel(me);

  // Remove admin panel from DOM entirely for non-admins — hiding the button
  // alone is insufficient because users can call openAdmin() from the console.
  if (!me.is_admin) {
    const adminOverlay = document.getElementById('admin-overlay');
    if (adminOverlay) adminOverlay.remove();
    const adminBtn = document.getElementById('admin-btn');
    if (adminBtn) adminBtn.remove();
  }

  // Load guilds (server-filtered by permissions)
  const guilds = await api('/api/guilds');
  state.guilds = guilds;
  renderGuildList();

  if (guilds.length === 1) {
    await selectGuild(guilds[0].id);
  }

  // Load more button
  document.getElementById('load-more-btn').addEventListener('click', async () => {
    const container = document.getElementById('messages-container');
    const prevScrollHeight = container.scrollHeight;
    await loadMessages(true);
    // Keep scroll position
    container.scrollTop = container.scrollHeight - prevScrollHeight;
  });

  // Toggle members
  document.getElementById('toggle-members').addEventListener('click', () => {
    const sidebar = document.getElementById('members-sidebar');
    sidebar.classList.toggle('hidden');
    document.getElementById('toggle-members').classList.toggle('active');
  });

  initSearch();

  // Show admin link only for admins — the actual page is server-gated at /admin
  if (me.is_admin) {
    const adminBtn = document.getElementById('admin-btn');
    if (adminBtn) adminBtn.style.display = '';
  }
}

document.addEventListener('DOMContentLoaded', init);

