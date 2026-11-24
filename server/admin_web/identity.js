// אימות זהות מתנדבים - דף ניהול. הטוקן נשמר רק במשתנה בזיכרון (לא localStorage / sessionStorage).
'use strict';
(function () {
  let token = null;
  let current = null;
  const $ = (id) => document.getElementById(id);

  async function api(method, path, body) {
    const r = await fetch('/v1' + path, {
      method,
      headers: Object.assign({ Authorization: 'Bearer ' + token }, body ? { 'Content-Type': 'application/json' } : {}),
      body: body ? JSON.stringify(body) : undefined,
      cache: 'no-store',
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const e = new Error(data.message || ('שגיאה ' + r.status));
      e.status = r.status;
      throw e;
    }
    return data;
  }

  function clearReview() {
    current = null;
    $('review').hidden = true;
    $('idImg').removeAttribute('src');          // לא משאירים את התמונה בדף אחרי ההחלטה
    $('selfieImg').removeAttribute('src');
    $('note').value = '';
  }

  async function loadQueue() {
    const data = await api('GET', '/admin/identity');
    const ul = $('queue');
    ul.textContent = '';
    $('queueEmpty').hidden = data.pending.length > 0;
    for (const p of data.pending) {
      const li = document.createElement('li');
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn-plain';
      const when = new Date(p.submitted_at * 1000).toLocaleString('he-IL');
      b.textContent = `${p.display_name} (${p.username}) · ת"ז ...${p.id_last4} · ${when}`;
      b.addEventListener('click', () => openReview(p.username));
      li.appendChild(b);
      ul.appendChild(li);
    }
  }

  async function openReview(username) {
    clearReview();
    $('reviewMsg').textContent = '';
    const d = await api('GET', '/admin/identity/' + encodeURIComponent(username));
    current = username;
    $('reviewTitle').textContent = `${d.display_name} (${d.username})`;
    $('reviewMeta').textContent = `4 ספרות אחרונות בת"ז: ${d.id_last4} · הועלה ${new Date(d.submitted_at * 1000).toLocaleString('he-IL')}`;
    const mime = (b64) => (b64.startsWith('iVBOR') ? 'image/png' : 'image/jpeg');
    $('idImg').src = `data:${mime(d.id_image)};base64,${d.id_image}`;
    $('selfieImg').src = `data:${mime(d.selfie)};base64,${d.selfie}`;
    $('review').hidden = false;
  }

  async function decide(approve) {
    if (!current) return;
    const note = $('note').value.trim();
    if (!approve && !note && !confirm('לדחות בלי הערה? המתנדב/ת לא יידע/תדע מה לתקן.')) return;
    try {
      await api('POST', `/admin/identity/${encodeURIComponent(current)}/decide`, { approve, note });
      const who = current;
      clearReview();
      $('reviewMsg').textContent = `${who}: ${approve ? 'אומת/ה' : 'נדחה/תה'} · התמונות נמחקו מהשרת`;
      $('review').hidden = false;
      await loadQueue();
    } catch (e) {
      $('reviewMsg').textContent = e.message;
    }
  }

  $('tokenForm').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    token = $('tokenInput').value.trim();
    $('tokenInput').value = '';
    try {
      await loadQueue();
      $('tokenView').hidden = true;
      $('queueView').hidden = false;
      $('forgetBtn').hidden = false;
    } catch (e) {
      token = null;
      $('tokenErr').textContent = e.status === 404 ? 'טוקן שגוי' : e.message;
    }
  });
  $('approveBtn').addEventListener('click', () => decide(true));
  $('rejectBtn').addEventListener('click', () => decide(false));
  $('forgetBtn').addEventListener('click', () => {
    token = null;
    clearReview();
    $('queueView').hidden = true;
    $('tokenView').hidden = false;
    $('forgetBtn').hidden = true;
  });
})();
