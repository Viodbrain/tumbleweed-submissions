const $ = (s, root = document) => root.querySelector(s);
const API = $('meta[name="api-base"]').content.replace(/\/$/, '');
let token = null;
function node(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; }
function notice(id, text, error = false) { const n = $(id); n.textContent = text; n.classList.toggle('error', error); }
async function api(path, options = {}) {
  const headers = {'Content-Type': 'application/json', ...options.headers};
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(API + path, {...options, headers, cache: 'no-store'});
  let body; try { body = await response.json(); } catch { throw new Error('The service is unavailable. Please try again.'); }
  if (!response.ok) {
    if (response.status === 401 && document.body.dataset.page === 'maintain') signOutUI();
    const message = Array.isArray(body.detail) ? body.detail.map(e => `${e.loc.slice(1).join('.')}: ${e.msg}`).join('; ') : body.detail;
    throw new Error(message || 'Could not complete the request. Please try again.');
  }
  return body;
}
async function busy(button, task) { button.disabled = true; try { await task(); } finally { button.disabled = false; } }
const fieldsMarkup = `<div class="form-grid">
<label class="full">What are you sharing?<select name="resource_type"><option value="place">A food location</option><option value="event">An upcoming event</option></select></label>
<label class="full">Name / event title<input name="name" minlength="2" maxlength="160" required></label>
<label class="full">Street address<input name="address" minlength="5" maxlength="300" autocomplete="street-address" required></label>
<label>ZIP code<input name="zip" inputmode="numeric" pattern="[0-9]{5}" maxlength="5" required></label>
<label>Area<select name="region"><option value="rural">Rural Washington</option><option value="seattle">Seattle</option></select></label>
<label>Latitude<input name="lat" type="number" step="any" min="-90" max="90" required placeholder="46.8262"></label>
<label>Longitude<input name="lng" type="number" step="any" min="-180" max="180" required placeholder="-119.1613"></label>
<p class="hint full">Copy verified coordinates from a map. For Washington, latitude is positive and longitude is negative.</p>
<label class="full" data-for="place">Location type<select name="kind"><option value="foodbank">Food bank / pantry</option><option value="grocery">Grocery store</option><option value="market">Farmers market</option><option value="meal">Community meal</option></select></label>
<label class="full" data-for="place">Public opening hours<input name="hours" maxlength="500" placeholder="e.g. Monday 1–4 pm"><span class="hint">Leave blank if unknown. Put any uncertainty in notes.</span></label>
<label class="full" data-for="place">Verified opening days<select name="days" multiple size="4"><option value="mon">Monday</option><option value="tue">Tuesday</option><option value="wed">Wednesday</option><option value="thu">Thursday</option><option value="fri">Friday</option><option value="sat">Saturday</option><option value="sun">Sunday</option></select><span class="hint">Optional. Select only verified days; Ctrl / ⌘ selects more than one.</span></label>
<label class="full" data-for="place"><input name="accepts_snap" type="checkbox">SNAP acceptance verified</label>
<label class="full" data-for="place">Public phone number<input name="phone" type="tel" maxlength="60"></label>
<label class="full" data-for="event">Venue reference<input name="place_id" maxlength="100"><span class="hint">Choose “Add an event here” on an approved location, or copy its reference.</span></label>
<label class="full" data-for="event">Venue name<input name="place_name" maxlength="160"></label>
<label class="full" data-for="event">Event start<input name="starts_at" type="datetime-local"><span class="hint time-zone"></span></label>
<label class="full">Verification source URL<input name="source_url" type="url" maxlength="2000" placeholder="https://organization.org/food-support" required><span class="hint">Use the organization’s website or another reliable public listing.</span></label>
<label class="full">Notes<textarea name="notes" maxlength="2000" placeholder="Eligibility, languages, access details, or information to confirm."></textarea></label></div>`;
function resourceForm(container, resource = {}) {
  container.innerHTML = fieldsMarkup;
  const type = $('[name=resource_type]', container);
  for (const [key, value] of Object.entries(resource)) {
    const input = $(`[name="${key}"]`, container); if (!input || value == null) continue;
    if (input.type === 'checkbox') input.checked = value;
    else if (input.multiple) for (const option of input.options) option.selected = value.includes(option.value);
    else if (key === 'starts_at') {
      const date = new Date(value); input.value = new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    } else input.value = value;
  }
  $('.time-zone', container).textContent = `Time zone: ${Intl.DateTimeFormat().resolvedOptions().timeZone}. Enter the event time in this zone.`;
  function sync() {
    for (const section of container.querySelectorAll('[data-for]')) {
      const active = section.dataset.for === type.value; section.hidden = !active;
      for (const input of section.querySelectorAll('input,select,textarea')) input.disabled = !active;
    }
    for (const name of ['place_id', 'place_name', 'starts_at']) $(`[name=${name}]`, container).required = type.value === 'event';
  }
  type.addEventListener('change', sync); sync();
}
function readResource(form) {
  const values = new FormData(form);
  const resource = {};
  for (const key of ['resource_type', 'name', 'address', 'zip', 'region', 'source_url']) resource[key] = values.get(key).trim();
  resource.lat = Number(values.get('lat')); resource.lng = Number(values.get('lng'));
  resource.notes = values.get('notes')?.trim() || null;
  if (resource.resource_type === 'place') {
    Object.assign(resource, {kind: values.get('kind'), hours: values.get('hours')?.trim() || null,
      days: values.getAll('days'), phone: values.get('phone')?.trim() || null, accepts_snap: values.has('accepts_snap')});
  } else Object.assign(resource, {place_id: values.get('place_id').trim(), place_name: values.get('place_name').trim(), starts_at: new Date(values.get('starts_at')).toISOString()});
  return resource;
}
function safeLink(url, label) {
  const parsed = new URL(url); if (!['https:', 'http:'].includes(parsed.protocol)) return node('span', label);
  const a = node('a', label); a.href = parsed.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a;
}
function card(resource, event = false) {
  const c = node('article', null, 'card');
  c.append(node('span', event ? 'Upcoming event' : ({foodbank: 'Food bank', grocery: 'Grocery', market: 'Market', meal: 'Community meal'}[resource.kind] || 'Location'), 'badge'));
  if (Number.isFinite(resource.distance_mi)) c.append(node('span', `${resource.distance_mi.toFixed(1)} mi away`, 'distance'));
  c.append(node('h3', resource.title || resource.name), node('p', resource.address, 'muted'));
  if (event) c.append(node('p', `${new Date(resource.starts_at).toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'})} · ${resource.place_name}`));
  if (resource.hours) c.append(node('p', resource.hours));
  if (resource.notes) c.append(node('p', resource.notes, 'muted'));
  const meta = node('div', null, 'meta');
  if (resource.accepts_snap) meta.append(node('span', '✓ SNAP accepted'));
  if (resource.phone) meta.append(node('span', resource.phone));
  if (resource.source_url) meta.append(safeLink(resource.source_url, 'View source ↗'));
  if (meta.childNodes.length) c.append(meta);
  if (!event && document.body.dataset.page === 'directory') {
    const add = node('button', 'Add an event here', 'secondary'); add.type = 'button';
    add.addEventListener('click', () => {
      resourceForm($('#resource-fields'), {resource_type: 'event', place_id: resource._id,
        place_name: resource.name, address: resource.address, zip: resource.zip || $('#search [name=zip]').value,
        region: resource.region || 'rural', lat: resource.location.coordinates[1], lng: resource.location.coordinates[0]});
      $('#contribute').scrollIntoView({behavior: 'smooth'});
    });
    c.append(add);
  }
  return c;
}
if (document.body.dataset.page === 'directory') {
  resourceForm($('#resource-fields'));
  let active = 'places'; let result = null; let offset = 0; let searchParams;
  function render() {
    const container = $('#cards'); container.replaceChildren(); if (!result) return;
    for (const row of result[active].items) container.append(card(row, active === 'events'));
    if (!result[active].items.length) {
      const empty = node('div', null, 'empty'); empty.append(node('h3', active === 'places' ? 'No locations found here yet.' : 'No upcoming events found.'), node('p', 'Try a wider area, or share a resource to help grow this directory.', 'muted')); container.append(empty);
    }
    $('#more').hidden = !(result.places.has_more || result.events.has_more);
    $('#place-count').textContent = `(${result.places.items.length})`; $('#event-count').textContent = `(${result.events.items.length})`;
  }
  for (const tab of document.querySelectorAll('[data-tab]')) tab.addEventListener('click', () => {
    active = tab.dataset.tab; for (const t of document.querySelectorAll('[data-tab]')) t.setAttribute('aria-selected', String(t === tab)); render();
  });
  async function search(more = false) {
    notice('#search-status', 'Looking for nearby resources…');
    try {
      const params = new URLSearchParams(searchParams); params.set('offset', String(more ? offset + 30 : 0));
      const next = await api(`/api/directory?${params}`);
      if (more) { offset += 30; for (const group of ['places', 'events']) { next[group].items = [...result[group].items, ...next[group].items]; } }
      else offset = 0;
      result = next; render(); notice('#search-status', `Showing approved resources within ${result.radius_mi} miles of ${result.zip}.`);
    } catch (error) { notice('#search-status', error.message, true); }
  }
  $('#search').addEventListener('submit', event => { event.preventDefault(); searchParams = new URLSearchParams(new FormData(event.target)); busy($('button[type=submit]', event.target), () => search()); });
  $('#more').addEventListener('click', () => busy($('#more'), () => search(true)));
  $('#submission').addEventListener('submit', event => {
    event.preventDefault(); const form = event.target;
    busy($('button[type=submit]', form), async () => {
      try { const response = await api('/api/submissions', {method: 'POST', body: JSON.stringify(readResource(form))});
        form.reset(); resourceForm($('#resource-fields'));
        notice('#submission-status', `Thank you! Your submission is pending review. Reference: ${response.submission_id}. It will appear after approval.`);
      } catch (error) { notice('#submission-status', error.message, true); }
    });
  });
}
function signOutUI() { token = null; $('#login-panel').hidden = false; $('#desk').hidden = true; $('#logout').hidden = true; $('#queue').replaceChildren(); $('#review').replaceChildren(); }
if (document.body.dataset.page === 'maintain') {
  $('#toggle-password').addEventListener('click', () => {
    const input = $('#maintainer-password'); const visible = input.type === 'password';
    input.type = visible ? 'text' : 'password';
    $('#toggle-password').textContent = visible ? 'Hide password' : 'Show password';
    $('#toggle-password').setAttribute('aria-pressed', String(visible));
  });
  let rows = []; let selected; let offset = 0;
  $('#login').addEventListener('submit', event => {
    event.preventDefault(); const form = event.target;
    busy($('button[type=submit]', form), async () => {
      try { const response = await api('/api/maintainer/login', {method: 'POST', body: JSON.stringify(Object.fromEntries(new FormData(form)))});
        token = response.access_token; form.reset(); $('#maintainer-password').type = 'password'; $('#toggle-password').textContent = 'Show password'; $('#toggle-password').setAttribute('aria-pressed', 'false'); notice('#login-status', ''); $('#login-panel').hidden = true; $('#desk').hidden = false; $('#logout').hidden = false; await loadQueue();
      } catch (error) { notice('#login-status', error.message, true); }
    });
  });
  $('#logout').addEventListener('click', () => busy($('#logout'), async () => {
    try { await api('/api/maintainer/logout', {method: 'POST'}); signOutUI(); }
    catch (error) { notice('#desk-status', `Could not revoke your session: ${error.message}`, true); }
  }));
  async function loadQueue() {
    try { const response = await api(`/api/moderation/submissions?status=${$('#queue-status').value}&offset=${offset}`);
      rows = response.items; selected = null; $('#queue').replaceChildren(); $('#review').replaceChildren(node('h2', 'Choose a submission'), node('p', 'Check its source and details before publishing.', 'muted'));
      if (!rows.length) $('#queue').append(node('p', 'No submissions in this queue.', 'muted'));
      for (const row of rows) { const b = node('button', null, 'queue-card'); b.append(node('span', row.resource.resource_type, 'eyebrow'), node('strong', row.resource.name), node('span', `${row.resource.zip} · ${new Date(row.created_at).toLocaleDateString()}`, 'small muted')); b.addEventListener('click', () => { for (const button of $('#queue').children) button.classList.remove('selected'); b.classList.add('selected'); selected = row; review(); }); $('#queue').append(b); }
      $('#next').hidden = !response.has_more; $('#previous').hidden = offset === 0;
    } catch (error) { notice('#desk-status', error.message, true); }
  }
  $('#refresh').addEventListener('click', () => busy($('#refresh'), loadQueue));
  $('#queue-status').addEventListener('change', () => { offset = 0; loadQueue(); });
  $('#next').addEventListener('click', () => { offset += 30; loadQueue(); });
  $('#previous').addEventListener('click', () => { offset = Math.max(0, offset - 30); loadQueue(); });
  function review() {
    const container = $('#review'); container.replaceChildren();
    const heading = node('div', null, 'review-title'); heading.append(node('h2', selected.resource.name), node('span', selected.status, 'badge')); container.append(heading);
    const source = node('p', null, 'source'); source.append(safeLink(selected.resource.source_url, 'Open verification source ↗')); container.append(source);
    const ref = node('p', `Reference ${selected._id} · revision ${selected.revision}`, 'hint'); container.append(ref);
    if (selected.status !== 'pending') {
      container.append(card({...selected.resource, title: selected.resource.name}, selected.resource.resource_type === 'event'));
      if (selected.review_note) container.append(node('p', selected.review_note, 'notice'));
      if (selected.status === 'approving') {
        container.append(node('p', 'The reviewed version is frozen. Retry to finish publication safely.', 'muted'));
        const retry = node('button', 'Retry publication'); retry.addEventListener('click', () => busy(retry, () => decision('approve', selected.review_note || 'Retry checked publication'))); container.append(retry);
      } return;
    }
    const form = node('form'); const fields = node('div'); resourceForm(fields, selected.resource); form.append(fields);
    const save = node('button', 'Save edits', 'secondary'); save.type = 'submit'; form.append(save); container.append(form);
    let dirty = false;
    form.addEventListener('input', () => { dirty = true; }); form.addEventListener('change', () => { dirty = true; });
    form.addEventListener('submit', event => { event.preventDefault(); busy(save, async () => {
      try { selected = await api(`/api/moderation/submissions/${selected._id}`, {method: 'PUT', body: JSON.stringify({revision: selected.revision, resource: readResource(form)})}); review(); notice('#desk-status', 'Edits saved. Review the saved details, then approve.'); }
      catch (error) { notice('#desk-status', error.message, true); }
    }); });
    const decisions = node('form'); const label = node('label', 'Review note'); const note = node('textarea'); note.name = 'note'; note.minLength = 3; note.maxLength = 1000; note.required = true; note.placeholder = 'What did you verify, or why is this being rejected?'; label.append(note); decisions.append(label);
    const checkLabel = node('label', null, 'small'); const check = node('input'); check.type = 'checkbox'; check.required = true; checkLabel.append(check, document.createTextNode('I checked the source, coordinates, and event time (when applicable).')); decisions.append(checkLabel);
    const actions = node('div', null, 'actions'); const approve = node('button', 'Approve & publish ↗'); approve.type = 'submit'; const reject = node('button', 'Reject', 'danger'); reject.type = 'button'; actions.append(approve, reject); decisions.append(actions); container.append(decisions);
    decisions.addEventListener('submit', event => { event.preventDefault(); if (dirty) { notice('#desk-status', 'Save your edits before approving.', true); return; } busy(approve, () => decision('approve', note.value)); });
    reject.addEventListener('click', () => { if (note.value.trim().length < 3) { notice('#desk-status', 'Add a review note before rejecting.', true); return; } busy(reject, () => decision('reject', note.value)); });
  }
  async function decision(action, note) {
    try { await api(`/api/moderation/submissions/${selected._id}/${action}`, {method: 'POST', body: JSON.stringify({revision: selected.revision, note})});
      notice('#desk-status', action === 'approve' ? 'Approved. This resource is now available in the public directory and agent searches.' : 'Submission rejected. It has not been published.'); await loadQueue();
    } catch (error) { notice('#desk-status', error.message, true); }
  }
}
