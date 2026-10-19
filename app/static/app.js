const $ = (s) => document.querySelector(s);
const state = { characters: [], selected: null, jobs: [] };
const toast = (message, error = false) => { const el = $('#toast'); el.textContent = message; el.className = `toast show${error ? ' error' : ''}`; clearTimeout(el.timer); el.timer = setTimeout(() => el.className = 'toast', 4200); };
const api = async (url, options = {}) => { const res = await fetch(url, options); if (!res.ok) { let detail; try { detail = (await res.json()).detail; } catch { detail = await res.text(); } throw new Error(detail || `Erro ${res.status}`); } return res.status === 204 ? null : res.json(); };

async function loadHealth() {
  const el = $('#system-status');
  try { const h = await api('/api/health'); if (h.ok) { el.className = 'status-pill ready'; el.innerHTML = '<span></span> sistema pronto'; } else { const issue = !h.ollama.online ? 'Ollama offline' : !h.ollama.model_available ? 'modelo Ollama ausente' : !h.comfyui.online ? 'ComfyUI offline' : h.comfyui.missing_models?.length ? 'modelos de imagem ausentes' : 'InstantID ausente'; el.className = 'status-pill error'; el.innerHTML = `<span></span> ${issue}`; el.title = h.comfyui.missing_models?.join(', ') || 'Abra o README para verificar a instalação'; } } catch { el.className = 'status-pill error'; el.innerHTML = '<span></span> serviços offline'; }
}

async function loadCharacters() {
  state.characters = await api('/api/characters');
  if (!state.characters.some(c => c.id === state.selected)) state.selected = state.characters[0]?.id || null;
  const list = $('#character-list');
  list.innerHTML = state.characters.map(c => `<button class="character ${c.id === state.selected ? 'selected' : ''}" data-id="${c.id}"><img src="${c.reference_url}" alt=""><span><strong>${escapeHtml(c.name)}</strong><small>Referência ativa</small></span><i class="tick"></i></button>`).join('');
  list.querySelectorAll('.character').forEach(b => b.onclick = () => { state.selected = b.dataset.id; loadCharacters(); });
  $('#empty-add').style.display = state.characters.length ? 'none' : 'grid';
}

async function loadGallery() {
  state.jobs = await api('/api/generations'); const gallery = $('#gallery');
  if (!state.jobs.length) { gallery.innerHTML = '<div class="empty-gallery">Suas imagens aparecerão aqui depois da primeira geração.</div>'; return; }
  gallery.innerHTML = state.jobs.map(j => j.status === 'completed' ? `<article class="gallery-card"><a href="${j.output_url}" target="_blank"><img src="${j.output_url}" alt="Imagem gerada"></a><div class="meta"><strong>${escapeHtml(j.character_name)}</strong><p>${escapeHtml(j.prompt)}</p></div></article>` : `<article class="gallery-card pending"><div><div class="loader" ${j.status === 'failed' ? 'style="animation:none;border-color:#d85d3b"' : ''}></div><strong>${j.status === 'failed' ? 'Não foi possível gerar' : 'Criando imagem...'}</strong><p>${escapeHtml(j.error || 'O processamento local pode levar alguns minutos.')}</p>${j.status === 'failed' ? `<button class="retry" data-retry="${j.id}">Usar descrição novamente</button>` : ''}</div></article>`).join('');
  gallery.querySelectorAll('[data-retry]').forEach(button => button.onclick = () => { const job = state.jobs.find(j => j.id === button.dataset.retry); if (!job) return; state.selected = job.character_id; $('#prompt').value = job.prompt; loadCharacters(); $('#generation-form').scrollIntoView({behavior:'smooth', block:'center'}); toast('Descrição recuperada. Clique em Gerar imagem.'); });
  if (state.jobs.some(j => ['queued','running'].includes(j.status))) setTimeout(loadGallery, 3000);
}

const escapeHtml = (s='') => s.replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const openDialog = () => $('#character-dialog').showModal();
$('#new-character').onclick = openDialog; $('#empty-add').onclick = openDialog; $('#close-dialog').onclick = () => $('#character-dialog').close();
$('#photo').onchange = (e) => { const file = e.target.files[0]; if (!file) return; $('#photo-preview').src = URL.createObjectURL(file); $('#dropzone').classList.add('has-image'); };
$('#character-form').onsubmit = async (e) => { e.preventDefault(); const data = new FormData(); data.append('name', $('#character-name').value); data.append('photo', $('#photo').files[0]); try { const c = await api('/api/characters', {method:'POST', body:data}); state.selected = c.id; e.target.reset(); $('#dropzone').classList.remove('has-image'); $('#character-dialog').close(); await loadCharacters(); toast('Personagem criado.'); } catch (err) { toast(err.message, true); } };
document.querySelectorAll('[data-prompt]').forEach(b => b.onclick = () => { $('#prompt').value = b.dataset.prompt; $('#prompt').focus(); });
$('#fidelity').oninput = e => $('#fidelity-value').value = `${e.target.value}%`;
$('#refresh').onclick = loadGallery;
$('#generation-form').onsubmit = async (e) => { e.preventDefault(); if (!state.selected) { openDialog(); toast('Adicione uma foto de referência primeiro.', true); return; } const [width,height] = $('#ratio').value.split('x').map(Number); const button = $('#generate-button'); button.disabled = true; button.querySelector('span:nth-child(2)').textContent = 'Preparando...'; try { await api('/api/generations', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({character_id:state.selected,prompt:$('#prompt').value,negative_prompt:$('#negative').value,width,height,fidelity:Number($('#fidelity').value)/100,enhance_prompt:$('#enhance').checked})}); toast('Geração iniciada na GPU local.'); $('#prompt').value = ''; await loadGallery(); } catch(err) { toast(err.message, true); } finally { button.disabled = false; button.querySelector('span:nth-child(2)').textContent = 'Gerar imagem'; } };

Promise.all([loadHealth(), loadCharacters(), loadGallery()]).catch(err => toast(err.message, true));
