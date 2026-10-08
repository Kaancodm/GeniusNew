const $ = (id) => document.getElementById(id);
const state = { user: null, csrf: null, pages: [], page: null, dirty: false, timer: null, mode: 'login', change: 0 };

async function api(path, options = {}) {
  const headers = { Accept: 'application/json', ...options.headers };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (state.csrf && !['GET', 'HEAD'].includes(options.method || 'GET')) headers['X-CSRF-Token'] = state.csrf;
  const response = await fetch('/api' + path, { credentials: 'same-origin', ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
  return data;
}

function toast(message) {
  $('toast').textContent = message;
  $('toast').classList.remove('hidden');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => $('toast').classList.add('hidden'), 3000);
}

function showAuth() {
  $('auth').classList.remove('hidden');
  $('workspace').classList.add('hidden');
  state.user = null;
  state.csrf = null;
  state.page = null;
}

async function enterWorkspace() {
  $('auth').classList.add('hidden');
  $('workspace').classList.remove('hidden');
  $('account-name').textContent = state.user.email;
  $('avatar').textContent = state.user.email.charAt(0).toUpperCase();
  await loadPages();
  const requested = new URLSearchParams(location.search).get('page');
  if (requested) {
    await openPage(requested).catch(() => { history.replaceState({}, '', '/'); });
  } else if (state.pages.length) {
    await openPage(state.pages[0].id);
  } else {
    showEmpty();
  }
}

function showEmpty() {
  state.page = null;
  $('empty-state').classList.remove('hidden');
  $('editor-view').classList.add('hidden');
  $('share-button').classList.add('hidden');
  $('breadcrumb-title').textContent = 'Home';
  $('save-status').textContent = '';
  renderPages();
}

function renderPages() {
  const list = $('page-list');
  list.replaceChildren();
  $('page-count').textContent = state.pages.length;
  for (const page of state.pages) {
    const button = document.createElement('button');
    button.className = 'page-item' + (state.page?.id === page.id ? ' active' : '');
    button.type = 'button';
    const glyph = document.createElement('span');
    glyph.className = 'page-glyph';
    glyph.textContent = '▤';
    const title = document.createElement('span');
    title.className = 'page-name';
    title.textContent = page.title || 'Untitled';
    button.append(glyph, title);
    if (page.shared) {
      const dot = document.createElement('span');
      dot.className = 'shared-dot';
      dot.title = 'Shared page';
      button.append(dot);
    }
    button.addEventListener('click', () => openPage(page.id).catch((error) => toast(error.message)));
    list.append(button);
  }
}

async function loadPages() {
  const data = await api('/pages');
  state.pages = data.pages;
  renderPages();
}

async function openPage(id) {
  if (state.dirty && state.page) await savePage();
  clearTimeout(state.timer);
  const data = await api('/pages/' + encodeURIComponent(id));
  state.page = data.page;
  state.dirty = false;
  state.change = 0;
  $('empty-state').classList.add('hidden');
  $('editor-view').classList.remove('hidden');
  $('page-title').value = data.page.title;
  $('page-body').innerHTML = data.page.body_html;
  $('breadcrumb-title').textContent = data.page.title || 'Untitled';
  $('owner-label').textContent = data.page.owner_email === state.user.email ? 'Created by you' : 'Shared by ' + data.page.owner_email;
  $('updated-label').textContent = 'Edited ' + new Date(data.page.updated_at * 1000).toLocaleDateString();
  $('save-status').textContent = 'All changes saved';
  const editable = data.page.role !== 'viewer';
  $('page-title').disabled = !editable;
  $('page-body').contentEditable = String(editable);
  $('page-body').dataset.placeholder = editable ? 'Start writing something wonderful...' : '';
  $('save-button').classList.toggle('hidden', !editable);
  $('toolbar').classList.toggle('hidden', !editable);
  $('share-button').classList.toggle('hidden', data.page.role !== 'owner');
  history.replaceState({}, '', '/?page=' + encodeURIComponent(id));
  renderPages();
  $('sidebar').classList.remove('open');
}

async function createPage() {
  try {
    if (state.dirty) await savePage();
    const data = await api('/pages', { method: 'POST', body: JSON.stringify({ title: 'Untitled', body_html: '' }) });
    await loadPages();
    await openPage(data.page.id);
    $('page-title').focus();
    $('page-title').select();
  } catch (error) { toast(error.message); }
}

function markDirty() {
  if (!state.page || state.page.role === 'viewer') return;
  state.dirty = true;
  state.change += 1;
  $('save-status').textContent = 'Unsaved changes';
  clearTimeout(state.timer);
  state.timer = setTimeout(() => savePage().catch((error) => toast(error.message)), 1200);
}

async function savePage() {
  if (!state.page || !state.dirty) return;
  clearTimeout(state.timer);
  const id = state.page.id;
  const title = $('page-title').value.trim() || 'Untitled';
  const body_html = $('page-body').innerHTML;
  const change = state.change;
  const version = state.page.version;
  $('save-status').textContent = 'Saving...';
  try {
    const data = await api('/pages/' + encodeURIComponent(id), { method: 'PUT', body: JSON.stringify({ title, body_html, version }) });
    if (state.page?.id !== id) return;
    state.page = data.page;
    if (change === state.change) {
      state.dirty = false;
      $('page-body').innerHTML = data.page.body_html;
      $('page-title').value = data.page.title;
    } else {
      state.timer = setTimeout(() => savePage().catch((error) => toast(error.message)), 300);
    }
    $('breadcrumb-title').textContent = data.page.title;
    $('updated-label').textContent = 'Edited ' + new Date(data.page.updated_at * 1000).toLocaleDateString();
    $('save-status').textContent = state.dirty ? 'Unsaved changes' : 'All changes saved';
    await loadPages();
  } catch (error) {
    $('save-status').textContent = 'Could not save';
    throw error;
  }
}

async function loadShares() {
  if (!state.page) return;
  const data = await api('/pages/' + encodeURIComponent(state.page.id) + '/shares');
  const list = $('share-list');
  list.replaceChildren();
  const owner = document.createElement('div');
  owner.className = 'person';
  const avatar = document.createElement('span');
  avatar.className = 'avatar';
  avatar.textContent = state.user.email.charAt(0).toUpperCase();
  const email = document.createElement('span');
  email.className = 'person-email';
  email.textContent = state.user.email + ' (you)';
  const role = document.createElement('span');
  role.className = 'person-role';
  role.textContent = 'Owner';
  owner.append(avatar, email, role);
  list.append(owner);
  for (const share of data.shares) {
    const row = document.createElement('div');
    row.className = 'person';
    const icon = document.createElement('span');
    icon.className = 'avatar';
    icon.textContent = share.email.charAt(0).toUpperCase();
    const name = document.createElement('span');
    name.className = 'person-email';
    name.textContent = share.email;
    const permission = document.createElement('span');
    permission.className = 'person-role';
    permission.textContent = share.role === 'editor' ? 'Can edit' : 'Can view';
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.title = 'Remove access';
    remove.setAttribute('aria-label', 'Remove access for ' + share.email);
    remove.textContent = '×';
    remove.addEventListener('click', async () => {
      try {
        await api('/pages/' + encodeURIComponent(state.page.id) + '/shares/' + encodeURIComponent(share.user_id), { method: 'DELETE' });
        await loadShares();
        await loadPages();
        toast('Access removed');
      } catch (error) { $('share-error').textContent = error.message; }
    });
    row.append(icon, name, permission, remove);
    list.append(row);
  }
}

function setAuthMode(mode) {
  state.mode = mode;
  $('login-tab').classList.toggle('active', mode === 'login');
  $('signup-tab').classList.toggle('active', mode === 'signup');
  $('auth-submit').firstChild.textContent = mode === 'login' ? 'Log in ' : 'Create account ';
  $('password').autocomplete = mode === 'login' ? 'current-password' : 'new-password';
  $('auth-error').textContent = '';
}

$('login-tab').addEventListener('click', () => setAuthMode('login'));
$('signup-tab').addEventListener('click', () => setAuthMode('signup'));
$('auth-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('auth-error').textContent = '';
  $('auth-submit').disabled = true;
  try {
    const data = await api('/' + state.mode, { method: 'POST', body: JSON.stringify({ email: $('email').value, password: $('password').value }) });
    state.user = data.user;
    state.csrf = data.csrf;
    $('password').value = '';
    await enterWorkspace();
  } catch (error) { $('auth-error').textContent = error.message; }
  finally { $('auth-submit').disabled = false; }
});
$('logout').addEventListener('click', async () => {
  try { await api('/logout', { method: 'POST' }); } catch (_) { /* The UI still drops its session. */ }
  history.replaceState({}, '', '/');
  showAuth();
});
$('new-page').addEventListener('click', createPage);
$('empty-new-page').addEventListener('click', createPage);
$('page-title').addEventListener('input', markDirty);
$('page-body').addEventListener('input', markDirty);
$('save-button').addEventListener('click', () => savePage().then(() => toast('Page saved')).catch((error) => toast(error.message)));
document.querySelectorAll('.toolbar [data-command]').forEach((button) => button.addEventListener('click', () => {
  $('page-body').focus();
  document.execCommand(button.dataset.command, false, button.dataset.value || null);
  markDirty();
}));
$('link-button').addEventListener('click', () => {
  const url = prompt('Link URL (https:// or mailto:)');
  if (!url || !/^(https?:\/\/|mailto:)/i.test(url)) return;
  $('page-body').focus();
  document.execCommand('createLink', false, url);
  markDirty();
});
$('share-button').addEventListener('click', async () => {
  if (state.dirty) await savePage().catch((error) => { toast(error.message); });
  $('share-error').textContent = '';
  $('share-modal').classList.remove('hidden');
  await loadShares().catch((error) => { $('share-error').textContent = error.message; });
});
$('close-share').addEventListener('click', () => $('share-modal').classList.add('hidden'));
$('share-modal').addEventListener('click', (event) => { if (event.target === $('share-modal')) $('share-modal').classList.add('hidden'); });
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') $('share-modal').classList.add('hidden'); });
$('share-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('share-error').textContent = '';
  try {
    await api('/pages/' + encodeURIComponent(state.page.id) + '/shares', { method: 'POST', body: JSON.stringify({ email: $('share-email').value, role: $('share-role').value }) });
    $('share-email').value = '';
    await loadShares();
    await loadPages();
    toast('Page shared');
  } catch (error) { $('share-error').textContent = error.message; }
});
$('menu').addEventListener('click', () => $('sidebar').classList.add('open'));
$('sidebar-toggle').addEventListener('click', () => $('sidebar').classList.remove('open'));
window.addEventListener('beforeunload', (event) => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
api('/me').then((data) => { state.user = data.user; state.csrf = data.csrf; return enterWorkspace(); }).catch(showAuth);
