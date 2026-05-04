// FabBoard — Paramètres (Phase 2)

const VIDEO_EXTENSIONS = new Set(['mp4', 'webm', 'ogg']);

function _isVideoFile(file) {
    if (!file || !file.name) return false;
    const ext = file.name.split('.').pop().toLowerCase();
    return VIDEO_EXTENSIONS.has(ext);
}

function _isVideoUrl(url) {
    if (!url) return false;
    const ext = url.split('?')[0].split('.').pop().toLowerCase();
    return VIDEO_EXTENSIONS.has(ext);
}

let sourceTypes = [];
let sources = [];
let sourceModal = null;

document.addEventListener('DOMContentLoaded', async () => {
    setupEventListeners();

    const modalElement = document.getElementById('modalSource');
    if (modalElement) {
        sourceModal = new bootstrap.Modal(modalElement);
    }

    await Promise.all([loadParametres(), loadSourceTypes()]);
    await loadSources();
    openCreateSourceModal();

    // Auto-refresh du statut des sources toutes les 30s
    setInterval(loadSources, 30000);
});

const FONT_FAMILY_MAP = {
    inter: "'Inter', sans-serif",
    roboto: "'Roboto', sans-serif",
    poppins: "'Poppins', sans-serif",
    montserrat: "'Montserrat', sans-serif",
    opensans: "'Open Sans', sans-serif",
    sourcesans: "'Source Sans 3', sans-serif",
    orbitron: "'Orbitron', sans-serif",
    rajdhani: "'Rajdhani', sans-serif",
    system: "system-ui, -apple-system, 'Segoe UI', sans-serif",
    serif: "Georgia, 'Times New Roman', serif",
    mono: "'Consolas', 'Courier New', monospace",
};

function applyFontFamily(fontFamilyKey) {
    const key = FONT_FAMILY_MAP[fontFamilyKey] ? fontFamilyKey : 'inter';
    document.documentElement.style.setProperty('--app-font-family', FONT_FAMILY_MAP[key]);
}

function getDefaultPauseSchedule() {
    const schedule = {};
    for (let day = 0; day < 7; day += 1) {
        schedule[String(day)] = {
            enabled: day >= 0 && day <= 4,
            start: '12:00',
            end: '13:00',
        };
    }
    return schedule;
}

function readPauseScheduleFromForm() {
    const schedule = {};
    for (let day = 0; day < 7; day += 1) {
        schedule[String(day)] = {
            enabled: !!document.getElementById(`pause-day-${day}-enabled`)?.checked,
            start: document.getElementById(`pause-day-${day}-start`)?.value || '12:00',
            end: document.getElementById(`pause-day-${day}-end`)?.value || '13:00',
        };
    }
    return schedule;
}

function applyPauseScheduleToForm(rawSchedule) {
    const fallback = getDefaultPauseSchedule();
    let schedule = fallback;

    if (typeof rawSchedule === 'string' && rawSchedule.trim()) {
        try {
            const parsed = JSON.parse(rawSchedule);
            if (parsed && typeof parsed === 'object') {
                schedule = { ...fallback, ...parsed };
            }
        } catch (error) {
            schedule = fallback;
        }
    }

    for (let day = 0; day < 7; day += 1) {
        const slot = schedule[String(day)] || fallback[String(day)];
        const enabledEl = document.getElementById(`pause-day-${day}-enabled`);
        const startEl = document.getElementById(`pause-day-${day}-start`);
        const endEl = document.getElementById(`pause-day-${day}-end`);
        if (enabledEl) enabledEl.checked = !!slot.enabled;
        if (startEl) startEl.value = slot.start || '12:00';
        if (endEl) endEl.value = slot.end || '13:00';
    }
}

function setupEventListeners() {
    document.getElementById('form-params-general').addEventListener('submit', (e) => {
        e.preventDefault();
        saveParametres();
    });

    const saveOverrideButton = document.getElementById('btn-save-override');
    if (saveOverrideButton) {
        saveOverrideButton.addEventListener('click', saveParametres);
    }

    document.getElementById('btn-save-source').addEventListener('click', saveSource);
    document.getElementById('btn-add-source').addEventListener('click', openCreateSourceModal);
    document.getElementById('btn-refresh-sources').addEventListener('click', loadSources);
    document.getElementById('source-type').addEventListener('change', onSourceTypeChange);
    document.getElementById('btn-test-source-modal').addEventListener('click', testSourceFromModal);
    document.getElementById('sources-table-body').addEventListener('click', onSourcesTableClick);
    document.getElementById('modalSource').addEventListener('hidden.bs.modal', openCreateSourceModal);

    const overrideMode = document.getElementById('param-display-override-mode');
    if (overrideMode) {
        overrideMode.addEventListener('change', toggleDisplayOverrideMode);
    }

    const pauseMode = document.getElementById('param-pause-mode');
    if (pauseMode) {
        pauseMode.addEventListener('change', togglePauseMode);
    }

    const manualShowReturn = document.getElementById('param-manual-show-return');
    if (manualShowReturn) {
        manualShowReturn.addEventListener('change', toggleManualReturnField);
    }

    const overrideImageFile = document.getElementById('param-display-override-image-file');
    if (overrideImageFile) {
        overrideImageFile.addEventListener('change', onDisplayOverrideImagePicked);
    }

    const pauseImageFile = document.getElementById('param-pause-image-file');
    if (pauseImageFile) {
        pauseImageFile.addEventListener('change', onPauseImagePicked);
    }

    const pauseScale = document.getElementById('param-pause-text-scale');
    if (pauseScale) {
        pauseScale.addEventListener('input', updateTextScaleBadges);
    }

    const manualScale = document.getElementById('param-manual-text-scale');
    if (manualScale) {
        manualScale.addEventListener('input', updateTextScaleBadges);
    }
}

function updateTextScaleBadges() {
    const pauseScaleEl = document.getElementById('param-pause-text-scale');
    const pauseScaleValueEl = document.getElementById('param-pause-text-scale-value');
    if (pauseScaleEl && pauseScaleValueEl) {
        pauseScaleValueEl.textContent = `${pauseScaleEl.value || '100'}%`;
    }

    const manualScaleEl = document.getElementById('param-manual-text-scale');
    const manualScaleValueEl = document.getElementById('param-manual-text-scale-value');
    if (manualScaleEl && manualScaleValueEl) {
        manualScaleValueEl.textContent = `${manualScaleEl.value || '100'}%`;
    }
}

async function loadParametres() {
    try {
        const params = await apiCall('/api/parametres');
        document.getElementById('param-fablab-name').value = params.fablab_name || "Loritz'Lab";
        document.getElementById('param-refresh').value = params.refresh_interval || 30;
        document.getElementById('param-theme').value = params.theme || 'light';

        const policeValue = params.police_dashboard || params.font_family || 'inter';
        const policeSelect = document.getElementById('param-police');
        const fontFamilySelect = document.getElementById('param-font-family');
        if (policeSelect) policeSelect.value = policeValue;
        if (fontFamilySelect) fontFamilySelect.value = policeValue;
        applyFontFamily(policeValue);

        const pauseEnabledEl = document.getElementById('param-pause-enabled');
        if (pauseEnabledEl) {
            pauseEnabledEl.checked = String(params.pause_schedule_enabled || '0') === '1';
        }
        applyPauseScheduleToForm(params.pause_weekly_schedule || '');

        const pauseTitleEl = document.getElementById('param-pause-title');
        if (pauseTitleEl) {
            pauseTitleEl.value = params.pause_title || 'Pause en cours';
        }

        const pauseMessageEl = document.getElementById('param-pause-message');
        if (pauseMessageEl) {
            pauseMessageEl.value = params.pause_message || '';
        }

        const pauseScaleEl = document.getElementById('param-pause-text-scale');
        if (pauseScaleEl) {
            pauseScaleEl.value = params.pause_text_scale || '100';
        }

        const pauseModeEl = document.getElementById('param-pause-mode');
        if (pauseModeEl) {
            pauseModeEl.value = params.pause_mode || 'text';
        }

        const pauseImageUrl = params.pause_image_url || '';
        const pauseImageUrlEl = document.getElementById('param-pause-image-url');
        if (pauseImageUrlEl) {
            pauseImageUrlEl.value = pauseImageUrl;
        }
        updatePauseImagePreview(pauseImageUrl);

        const manualEnabledEl = document.getElementById('param-manual-unavailable-enabled');
        if (manualEnabledEl) {
            manualEnabledEl.checked = String(params.manual_unavailable_enabled || params.display_override_enabled || '0') === '1';
        }

        const manualShowReturnEl = document.getElementById('param-manual-show-return');
        if (manualShowReturnEl) {
            manualShowReturnEl.checked = String(params.manual_unavailable_show_return || '0') === '1';
        }

        const manualReturnTimeEl = document.getElementById('param-manual-return-time');
        if (manualReturnTimeEl) {
            manualReturnTimeEl.value = params.manual_unavailable_return_time || '14:00';
        }

        const modeEl = document.getElementById('param-display-override-mode');
        if (modeEl) {
            modeEl.value = params.manual_unavailable_mode || params.display_override_mode || 'text';
        }

        const titleEl = document.getElementById('param-display-override-title');
        if (titleEl) {
            titleEl.value = params.manual_unavailable_title || params.display_override_title || 'FabLab indisponible';
        }

        const messageEl = document.getElementById('param-display-override-message');
        if (messageEl) {
            messageEl.value = params.manual_unavailable_message || params.display_override_message || '';
        }

        const imageUrl = params.manual_unavailable_image_url || params.display_override_image_url || '';
        const imageUrlEl = document.getElementById('param-display-override-image-url');
        if (imageUrlEl) {
            imageUrlEl.value = imageUrl;
        }
        updateDisplayOverrideImagePreview(imageUrl);

        const bgColorEl = document.getElementById('param-display-override-bg-color');
        if (bgColorEl) {
            bgColorEl.value = params.manual_unavailable_bg_color || params.display_override_bg_color || '#0b1120';
        }

        const textColorEl = document.getElementById('param-display-override-text-color');
        if (textColorEl) {
            textColorEl.value = params.manual_unavailable_text_color || params.display_override_text_color || '#f8fafc';
        }

        const manualScaleEl = document.getElementById('param-manual-text-scale');
        if (manualScaleEl) {
            manualScaleEl.value = params.manual_unavailable_text_scale || '100';
        }

        toggleDisplayOverrideMode();
        togglePauseMode();
        toggleManualReturnField();
        updateTextScaleBadges();
    } catch (error) {
        console.error('Erreur chargement paramètres:', error);
    }
}

async function saveParametres() {
    const uploadedManualImageUrl = await uploadDisplayOverrideImageIfNeeded();
    if (uploadedManualImageUrl === null) {
        return;
    }

    const uploadedPauseImageUrl = await uploadPauseImageIfNeeded();
    if (uploadedPauseImageUrl === null) {
        return;
    }

    const params = {
        fablab_name: document.getElementById('param-fablab-name').value.trim(),
        refresh_interval: document.getElementById('param-refresh').value,
        theme: document.getElementById('param-theme').value,
        police_dashboard: (document.getElementById('param-police')?.value
            || document.getElementById('param-font-family')?.value
            || 'inter'),
        pause_schedule_enabled: document.getElementById('param-pause-enabled')?.checked ? '1' : '0',
        pause_weekly_schedule: JSON.stringify(readPauseScheduleFromForm()),
        pause_title: (document.getElementById('param-pause-title')?.value || 'Pause en cours').trim(),
        pause_message: (document.getElementById('param-pause-message')?.value || '').trim(),
        pause_text_scale: (document.getElementById('param-pause-text-scale')?.value || '100'),
        pause_mode: (document.getElementById('param-pause-mode')?.value || 'text'),
        pause_image_url: uploadedPauseImageUrl || document.getElementById('param-pause-image-url')?.value || '',
        manual_unavailable_enabled: document.getElementById('param-manual-unavailable-enabled')?.checked ? '1' : '0',
        manual_unavailable_show_return: document.getElementById('param-manual-show-return')?.checked ? '1' : '0',
        manual_unavailable_return_time: document.getElementById('param-manual-return-time')?.value || '14:00',
        manual_unavailable_mode: (document.getElementById('param-display-override-mode')?.value || 'text'),
        manual_unavailable_title: (document.getElementById('param-display-override-title')?.value || '').trim(),
        manual_unavailable_message: (document.getElementById('param-display-override-message')?.value || '').trim(),
        manual_unavailable_image_url: uploadedManualImageUrl || document.getElementById('param-display-override-image-url')?.value || '',
        manual_unavailable_bg_color: document.getElementById('param-display-override-bg-color')?.value || '#0b1120',
        manual_unavailable_text_color: document.getElementById('param-display-override-text-color')?.value || '#f8fafc',
        manual_unavailable_text_scale: (document.getElementById('param-manual-text-scale')?.value || '100'),
    };

    if (!params.manual_unavailable_title) {
        params.manual_unavailable_title = 'FabLab indisponible';
    }

    if (!params.fablab_name) {
        showToast('Le nom du fablab est requis', 'warning');
        return;
    }

    try {
        await Promise.all([
            apiCall('/api/parametres/fablab_name', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.fablab_name }),
            }),
            apiCall('/api/parametres/refresh_interval', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.refresh_interval }),
            }),
            apiCall('/api/parametres/theme', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.theme }),
            }),
            apiCall('/api/parametres/police_dashboard', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.police_dashboard }),
            }),
            // Compatibilite avec les versions qui lisent encore font_family.
            apiCall('/api/parametres/font_family', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.police_dashboard }),
            }),
            apiCall('/api/parametres/pause_schedule_enabled', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.pause_schedule_enabled }),
            }),
            apiCall('/api/parametres/pause_weekly_schedule', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.pause_weekly_schedule }),
            }),
            apiCall('/api/parametres/pause_title', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.pause_title }),
            }),
            apiCall('/api/parametres/pause_message', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.pause_message }),
            }),
            apiCall('/api/parametres/pause_text_scale', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.pause_text_scale }),
            }),
            apiCall('/api/parametres/pause_mode', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.pause_mode }),
            }),
            apiCall('/api/parametres/pause_image_url', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.pause_image_url }),
            }),
            apiCall('/api/parametres/manual_unavailable_enabled', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.manual_unavailable_enabled }),
            }),
            apiCall('/api/parametres/manual_unavailable_show_return', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.manual_unavailable_show_return }),
            }),
            apiCall('/api/parametres/manual_unavailable_return_time', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.manual_unavailable_return_time }),
            }),
            apiCall('/api/parametres/manual_unavailable_mode', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.manual_unavailable_mode }),
            }),
            apiCall('/api/parametres/manual_unavailable_title', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.manual_unavailable_title }),
            }),
            apiCall('/api/parametres/manual_unavailable_message', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.manual_unavailable_message }),
            }),
            apiCall('/api/parametres/manual_unavailable_image_url', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.manual_unavailable_image_url }),
            }),
            apiCall('/api/parametres/manual_unavailable_bg_color', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.manual_unavailable_bg_color }),
            }),
            apiCall('/api/parametres/manual_unavailable_text_color', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.manual_unavailable_text_color }),
            }),
            apiCall('/api/parametres/manual_unavailable_text_scale', {
                method: 'PUT',
                body: JSON.stringify({ valeur: params.manual_unavailable_text_scale }),
            }),
        ]);

        applyFontFamily(params.police_dashboard);
        showToast('Paramètres enregistrés', 'success');
    } catch (error) {
        showToast(`Erreur sauvegarde: ${error.message}`, 'error');
    }
}

function toggleDisplayOverrideMode() {
    const mode = document.getElementById('param-display-override-mode')?.value || 'text';
    const textFields = document.getElementById('display-override-text-fields');
    const imageFields = document.getElementById('display-override-image-fields');

    if (textFields) {
        textFields.classList.toggle('d-none', mode !== 'text');
    }
    if (imageFields) {
        imageFields.classList.toggle('d-none', mode !== 'image');
    }
}

function togglePauseMode() {
    const mode = document.getElementById('param-pause-mode')?.value || 'text';
    const imageFields = document.getElementById('pause-image-fields');
    if (imageFields) {
        imageFields.classList.toggle('d-none', mode !== 'image');
    }
}

function toggleManualReturnField() {
    const showReturn = !!document.getElementById('param-manual-show-return')?.checked;
    const returnField = document.getElementById('manual-return-time-field');
    if (returnField) {
        returnField.classList.toggle('d-none', !showReturn);
    }
}

function updateDisplayOverrideImagePreview(url) {
    const imgPreview = document.getElementById('display-override-image-preview');
    const vidPreview = document.getElementById('display-override-video-preview');
    if (!imgPreview) return;

    if (!url) {
        imgPreview.classList.add('d-none');
        imgPreview.removeAttribute('src');
        if (vidPreview) { vidPreview.classList.add('d-none'); vidPreview.removeAttribute('src'); }
        return;
    }

    if (_isVideoUrl(url)) {
        imgPreview.classList.add('d-none');
        imgPreview.removeAttribute('src');
        if (vidPreview) { vidPreview.src = url; vidPreview.classList.remove('d-none'); }
    } else {
        if (vidPreview) { vidPreview.classList.add('d-none'); vidPreview.removeAttribute('src'); }
        imgPreview.src = url;
        imgPreview.classList.remove('d-none');
    }
}

function onDisplayOverrideImagePicked(event) {
    const file = event.target.files?.[0];
    if (!file) return;

    const imgPreview = document.getElementById('display-override-image-preview');
    const vidPreview = document.getElementById('display-override-video-preview');
    const objectUrl = URL.createObjectURL(file);

    if (_isVideoFile(file)) {
        if (imgPreview) { imgPreview.classList.add('d-none'); imgPreview.removeAttribute('src'); }
        if (vidPreview) { vidPreview.src = objectUrl; vidPreview.classList.remove('d-none'); }
    } else {
        if (vidPreview) { vidPreview.classList.add('d-none'); vidPreview.removeAttribute('src'); }
        if (imgPreview) { imgPreview.src = objectUrl; imgPreview.classList.remove('d-none'); }
    }
}

function updatePauseImagePreview(url) {
    const imgPreview = document.getElementById('pause-image-preview');
    const vidPreview = document.getElementById('pause-video-preview');
    if (!imgPreview) return;

    if (!url) {
        imgPreview.classList.add('d-none');
        imgPreview.removeAttribute('src');
        if (vidPreview) { vidPreview.classList.add('d-none'); vidPreview.removeAttribute('src'); }
        return;
    }

    if (_isVideoUrl(url)) {
        imgPreview.classList.add('d-none');
        imgPreview.removeAttribute('src');
        if (vidPreview) { vidPreview.src = url; vidPreview.classList.remove('d-none'); }
    } else {
        if (vidPreview) { vidPreview.classList.add('d-none'); vidPreview.removeAttribute('src'); }
        imgPreview.src = url;
        imgPreview.classList.remove('d-none');
    }
}

function onPauseImagePicked(event) {
    const file = event.target.files?.[0];
    if (!file) return;

    const imgPreview = document.getElementById('pause-image-preview');
    const vidPreview = document.getElementById('pause-video-preview');
    const objectUrl = URL.createObjectURL(file);

    if (_isVideoFile(file)) {
        if (imgPreview) { imgPreview.classList.add('d-none'); imgPreview.removeAttribute('src'); }
        if (vidPreview) { vidPreview.src = objectUrl; vidPreview.classList.remove('d-none'); }
    } else {
        if (vidPreview) { vidPreview.classList.add('d-none'); vidPreview.removeAttribute('src'); }
        if (imgPreview) { imgPreview.src = objectUrl; imgPreview.classList.remove('d-none'); }
    }
}

async function uploadDisplayOverrideImageIfNeeded() {
    const mode = document.getElementById('param-display-override-mode')?.value || 'text';
    if (mode !== 'image') {
        return document.getElementById('param-display-override-image-url')?.value || '';
    }

    const fileInput = document.getElementById('param-display-override-image-file');
    const file = fileInput?.files?.[0];
    if (!file) {
        return document.getElementById('param-display-override-image-url')?.value || '';
    }

    const isVideo = _isVideoFile(file);
    const endpoint = isVideo ? '/api/upload-video' : '/api/upload';
    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            body: formData,
        });

        const result = await response.json();
        if (!response.ok || !result.success || !result.url) {
            throw new Error(result.error || 'Upload média impossible');
        }

        const imageUrlEl = document.getElementById('param-display-override-image-url');
        if (imageUrlEl) {
            imageUrlEl.value = result.url;
        }
        updateDisplayOverrideImagePreview(result.url);
        return result.url;
    } catch (error) {
        showToast(`Erreur upload média: ${error.message}`, 'error');
        return null;
    }
}

async function uploadPauseImageIfNeeded() {
    const mode = document.getElementById('param-pause-mode')?.value || 'text';
    if (mode !== 'image') {
        return document.getElementById('param-pause-image-url')?.value || '';
    }

    const fileInput = document.getElementById('param-pause-image-file');
    const file = fileInput?.files?.[0];
    if (!file) {
        return document.getElementById('param-pause-image-url')?.value || '';
    }

    const isVideo = _isVideoFile(file);
    const endpoint = isVideo ? '/api/upload-video' : '/api/upload';
    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            body: formData,
        });

        const result = await response.json();
        if (!response.ok || !result.success || !result.url) {
            throw new Error(result.error || 'Upload média pause impossible');
        }

        const imageUrlEl = document.getElementById('param-pause-image-url');
        if (imageUrlEl) {
            imageUrlEl.value = result.url;
        }
        updatePauseImagePreview(result.url);
        return result.url;
    } catch (error) {
        showToast(`Erreur upload média pause: ${error.message}`, 'error');
        return null;
    }
}

async function loadSourceTypes() {
    try {
        const result = await apiCall('/api/sources/types');
        sourceTypes = result.data || [];
    } catch (error) {
        console.warn('Impossible de charger les types depuis API, fallback local:', error);
        const host = window.location.hostname || 'localhost';
        const fabtrackDefaultUrl = `http://${host}:5555`;
        sourceTypes = [
            { code: 'fabtrack', label: 'Fabtrack', description: 'Statistiques et consommations Fabtrack', default_url: fabtrackDefaultUrl },
            { code: 'repetier', label: 'Repetier Server', description: 'Etat des imprimantes 3D', default_url: 'http://localhost:3344' },
            { code: 'nextcloud_caldav', label: 'Nextcloud CalDAV', description: 'Calendrier externe', default_url: 'https://cloud.exemple.fr/remote.php/dav/calendars/user/calendrier' },
            { code: 'prusalink', label: 'PrusaLink', description: 'Imprimantes Prusa', default_url: 'http://localhost:8080' },
            { code: 'openweathermap', label: 'OpenWeatherMap', description: 'Données météo', default_url: 'https://api.openweathermap.org' },
            { code: 'rss', label: 'Flux RSS', description: 'Flux RSS ou Atom', default_url: 'https://example.com/feed.xml' },
            { code: 'http', label: 'HTTP/REST', description: 'Endpoint HTTP générique', default_url: 'https://api.example.com/data' },
        ];
    }

    renderSourceTypeOptions();
}

function renderSourceTypeOptions(selectedCode = '') {
    const select = document.getElementById('source-type');
    if (!select) return;

    const options = ['<option value="">Choisir...</option>'];
    for (const type of sourceTypes) {
        const selected = type.code === selectedCode ? 'selected' : '';
        options.push(`<option value="${escapeHtml(type.code)}" ${selected}>${escapeHtml(type.label)}</option>`);
    }
    select.innerHTML = options.join('');
}

async function loadSources() {
    const tbody = document.getElementById('sources-table-body');
    tbody.innerHTML = '<tr><td colspan="6" class="text-center py-4"><div class="spinner-border text-primary" role="status"></div></td></tr>';

    try {
        const result = await apiCall('/api/sources');
        sources = result.data || [];
        renderSourcesTable();
    } catch (error) {
        tbody.innerHTML = `<tr><td colspan="6" class="text-center text-danger py-4">Erreur chargement des sources: ${escapeHtml(error.message)}</td></tr>`;
    }
}

function renderSourcesTable() {
    const tbody = document.getElementById('sources-table-body');
    if (!tbody) return;

    if (sources.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center py-4 text-muted">Aucune source configurée</td></tr>';
        return;
    }

    tbody.innerHTML = sources.map((source) => {
        const typeMeta = sourceTypes.find((t) => t.code === source.type);
        const typeLabel = typeMeta ? typeMeta.label : source.type;
        const state = renderSourceStateBadge(source);
        const lastTest = source.derniere_sync ? formatDate(source.derniere_sync) : 'Jamais';
        const credentialsBadge = source.has_credentials
            ? '<span class="badge text-bg-light border ms-2"><i class="bi bi-key"></i> credentials</span>'
            : '';

        const errorLine = source.derniere_erreur
            ? `<div class="small text-danger mt-1">${escapeHtml(source.derniere_erreur)}</div>`
            : '';

        return `
            <tr>
                <td>
                    <strong>${escapeHtml(source.nom)}</strong>
                    ${credentialsBadge}
                    ${errorLine}
                </td>
                <td><span class="badge text-bg-secondary">${escapeHtml(typeLabel)}</span></td>
                <td><code>${escapeHtml(source.url)}</code></td>
                <td>${state}</td>
                <td>${escapeHtml(lastTest)}</td>
                <td class="text-end">
                    <div class="btn-group btn-group-sm" role="group">
                        <button class="btn btn-outline-secondary" data-action="toggle" data-id="${source.id}" title="Activer / désactiver">
                            <i class="bi ${source.actif ? 'bi-toggle-on' : 'bi-toggle-off'}"></i>
                        </button>
                        <button class="btn btn-outline-info" data-action="resync" data-id="${source.id}" title="Forcer re-sync">
                            <i class="bi bi-arrow-repeat"></i>
                        </button>
                        <button class="btn btn-outline-primary" data-action="test" data-id="${source.id}" title="Tester la connexion">
                            <i class="bi bi-plug"></i>
                        </button>
                        <button class="btn btn-outline-warning" data-action="edit" data-id="${source.id}" title="Modifier">
                            <i class="bi bi-pencil"></i>
                        </button>
                        <button class="btn btn-outline-danger" data-action="delete" data-id="${source.id}" title="Supprimer">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function renderSourceStateBadge(source) {
    if (!source.actif) {
        return '<span class="badge text-bg-secondary">Inactif</span>';
    }
    if (source.derniere_erreur) {
        return '<span class="badge text-bg-danger">Erreur</span>';
    }
    if (source.derniere_sync) {
        return '<span class="badge text-bg-success">OK</span>';
    }
    return '<span class="badge text-bg-warning">Jamais testé</span>';
}

function onSourcesTableClick(event) {
    const button = event.target.closest('button[data-action]');
    if (!button) return;

    const sourceId = parseInt(button.dataset.id, 10);
    const action = button.dataset.action;

    if (!sourceId || !action) return;

    if (action === 'edit') {
        openEditSourceModal(sourceId);
        return;
    }

    if (action === 'delete') {
        deleteSource(sourceId);
        return;
    }

    if (action === 'test') {
        testSource(sourceId, button);
        return;
    }

    if (action === 'toggle') {
        toggleSourceActive(sourceId, button);
    }

    if (action === 'resync') {
        resyncSource(sourceId, button);
    }
}

function openCreateSourceModal() {
    document.getElementById('source-id').value = '';
    document.getElementById('modal-source-title').textContent = 'Nouvelle Source de Données';
    document.getElementById('btn-save-source').innerHTML = '<i class="bi bi-check-circle"></i> Créer la source';
    document.getElementById('btn-test-source-modal').classList.add('d-none');

    document.getElementById('form-source').reset();
    document.getElementById('source-sync-interval').value = 60;
    document.getElementById('source-actif').checked = true;
    document.getElementById('source-credentials-extra').value = '';
    renderSourceTypeOptions();
    onSourceTypeChange();
}

function openEditSourceModal(sourceId) {
    const source = sources.find((s) => s.id === sourceId);
    if (!source) {
        showToast('Source introuvable', 'error');
        return;
    }

    document.getElementById('source-id').value = String(source.id);
    document.getElementById('modal-source-title').textContent = `Modifier: ${source.nom}`;
    document.getElementById('btn-save-source').innerHTML = '<i class="bi bi-check-circle"></i> Enregistrer';
    document.getElementById('btn-test-source-modal').classList.remove('d-none');

    renderSourceTypeOptions(source.type);
    document.getElementById('source-nom').value = source.nom;
    document.getElementById('source-url').value = source.url;
    document.getElementById('source-sync-interval').value = source.sync_interval_sec || 60;
    document.getElementById('source-actif').checked = source.actif === 1;

    // Ne jamais préremplir les credentials.
    document.getElementById('source-username').value = '';
    document.getElementById('source-password').value = '';
    document.getElementById('source-apikey').value = '';
    document.getElementById('source-city').value = '';
    document.getElementById('source-credentials-extra').value = '';

    onSourceTypeChange();
    if (sourceModal) {
        sourceModal.show();
    }
}

function onSourceTypeChange() {
    const sourceType = document.getElementById('source-type').value;
    const urlInput = document.getElementById('source-url');
    const help = document.getElementById('source-type-help');

    const typeMeta = sourceTypes.find((t) => t.code === sourceType);
    if (!typeMeta) {
        help.textContent = 'Sélectionnez un type pour obtenir une URL suggérée.';
        return;
    }

    help.textContent = `${typeMeta.description}. URL recommandée: ${typeMeta.default_url}`;
    if (!urlInput.value.trim()) {
        urlInput.value = typeMeta.default_url;
    }
}

function buildCredentialsPayload(isEdit) {
    const username = document.getElementById('source-username').value.trim();
    const password = document.getElementById('source-password').value.trim();
    const apikey = document.getElementById('source-apikey').value.trim();
    const city = document.getElementById('source-city').value.trim();
    const extraRaw = document.getElementById('source-credentials-extra').value.trim();

    const credentials = {};
    if (username) credentials.username = username;
    if (password) credentials.password = password;
    if (apikey) credentials.apikey = apikey;
    if (city) credentials.city = city;

    if (extraRaw) {
        let parsedExtra;
        try {
            parsedExtra = JSON.parse(extraRaw);
        } catch (error) {
            throw new Error('JSON additionnel credentials invalide');
        }
        if (!parsedExtra || typeof parsedExtra !== 'object' || Array.isArray(parsedExtra)) {
            throw new Error('Le JSON additionnel doit être un objet');
        }
        Object.assign(credentials, parsedExtra);
    }

    // En édition, ne rien envoyer signifie "conserver les credentials existants".
    if (isEdit && Object.keys(credentials).length === 0) {
        return null;
    }

    return credentials;
}

async function saveSource() {
    const sourceId = document.getElementById('source-id').value;
    const isEdit = Boolean(sourceId);

    const nom = document.getElementById('source-nom').value.trim();
    const type = document.getElementById('source-type').value;
    const url = document.getElementById('source-url').value.trim();
    const syncInterval = parseInt(document.getElementById('source-sync-interval').value, 10);
    const actif = document.getElementById('source-actif').checked ? 1 : 0;

    if (!nom || !type || !url) {
        showToast('Nom, type et URL sont obligatoires', 'warning');
        return;
    }

    if (!Number.isInteger(syncInterval) || syncInterval < 10 || syncInterval > 3600) {
        showToast('Intervalle de sync invalide (10 à 3600 sec)', 'warning');
        return;
    }

    const payload = {
        nom,
        type,
        url,
        sync_interval_sec: syncInterval,
        actif,
    };

    try {
        const credentials = buildCredentialsPayload(isEdit);
        if (credentials !== null) {
            payload.credentials = credentials;
        }
    } catch (error) {
        showToast(error.message, 'error');
        return;
    }

    const saveButton = document.getElementById('btn-save-source');
    setButtonBusy(saveButton, true);

    try {
        if (isEdit) {
            await apiCall(`/api/sources/${sourceId}`, {
                method: 'PUT',
                body: JSON.stringify(payload),
            });
            showToast('Source mise à jour', 'success');
        } else {
            await apiCall('/api/sources', {
                method: 'POST',
                body: JSON.stringify(payload),
            });
            showToast('Source créée', 'success');
        }

        if (sourceModal) {
            sourceModal.hide();
        }
        await loadSources();
    } catch (error) {
        showToast(`Erreur sauvegarde source: ${error.message}`, 'error');
    } finally {
        setButtonBusy(saveButton, false);
    }
}

async function deleteSource(sourceId) {
    const source = sources.find((s) => s.id === sourceId);
    if (!source) {
        showToast('Source introuvable', 'error');
        return;
    }

    const confirmDelete = confirm(`Supprimer la source "${source.nom}" ?`);
    if (!confirmDelete) return;

    try {
        await apiCall(`/api/sources/${sourceId}`, { method: 'DELETE' });
        showToast('Source supprimée', 'success');
        await loadSources();
    } catch (error) {
        showToast(`Erreur suppression: ${error.message}`, 'error');
    }
}

async function toggleSourceActive(sourceId, button) {
    const source = sources.find((s) => s.id === sourceId);
    if (!source) {
        showToast('Source introuvable', 'error');
        return;
    }

    const nextActif = source.actif === 1 ? 0 : 1;
    setButtonBusy(button, true);

    try {
        await apiCall(`/api/sources/${sourceId}`, {
            method: 'PUT',
            body: JSON.stringify({ actif: nextActif }),
        });

        showToast(nextActif ? 'Source activée' : 'Source désactivée', 'success');
        await loadSources();
    } catch (error) {
        showToast(`Erreur mise à jour statut: ${error.message}`, 'error');
    } finally {
        setButtonBusy(button, false);
    }
}

async function testSource(sourceId, button = null) {
    if (button) {
        setButtonBusy(button, true);
    }

    try {
        const result = await apiCall(`/api/sources/${sourceId}/test`, { method: 'POST' });
        const details = result.summary ? ` (${JSON.stringify(result.summary)})` : '';
        showToast(`Test OK${details}`, 'success');
    } catch (error) {
        showToast(`Test KO: ${error.message}`, 'error');
    } finally {
        if (button) {
            setButtonBusy(button, false);
        }
        await loadSources();
    }
}

async function resyncSource(sourceId, button = null) {
    if (button) {
        setButtonBusy(button, true);
    }

    try {
        await apiCall(`/api/sources/${sourceId}/resync`, { method: 'POST' });
        showToast('Re-synchronisation lancée', 'success');
    } catch (error) {
        showToast(`Erreur resync: ${error.message}`, 'error');
    } finally {
        if (button) {
            setButtonBusy(button, false);
        }
        await loadSources();
    }
}

async function testSourceFromModal() {
    const sourceId = parseInt(document.getElementById('source-id').value, 10);
    if (!sourceId) {
        showToast('Enregistrez d\'abord la source avant de la tester', 'warning');
        return;
    }

    const button = document.getElementById('btn-test-source-modal');
    await testSource(sourceId, button);
}

function setButtonBusy(button, isBusy) {
    if (!button) return;

    if (isBusy) {
        if (!button.dataset.originalHtml) {
            button.dataset.originalHtml = button.innerHTML;
        }
        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status"></span>';
        return;
    }

    button.disabled = false;
    if (button.dataset.originalHtml) {
        button.innerHTML = button.dataset.originalHtml;
    }
}

// ================================
// DONNÉES DE DÉMONSTRATION
// ================================

async function generateDemoSlides() {
    const btn = document.querySelector('button[onclick="generateDemoSlides()"]');
    const resultDiv = document.getElementById('demoSlidesResult');
    
    // Désactiver le bouton et afficher un spinner
    btn.disabled = true;
    btn.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Génération en cours...';
    
    resultDiv.style.display = 'none';
    
    try {
        const result = await apiCall('/api/slides/demo/generate', {
            method: 'POST'
        });
        
        if (result.success) {
            // Affichage du succès
            resultDiv.innerHTML = `
                <div class="alert alert-success small">
                    <i class="bi bi-check-circle me-2"></i>
                    <strong>${result.message}</strong>
                    <ul class="mt-2 mb-0">
                        <li>${result.details.slides_actives}</li>
                        <li>${result.details.widgets_disponibles}</li>
                        <li>Intervalle: ${result.details.intervalle}</li>
                        <li>Layout: ${result.details.layout}</li>
                        <li>Source: ${result.details.source_fabtrack}</li>
                    </ul>
                </div>
            `;
            
            showToast('✅ Slides de démonstration générées!', 'success');
            
            // Proposer d'aller voir le dashboard
            setTimeout(() => {
                if (confirm('Souhaitez-vous voir les slides en action sur le dashboard TV ?')) {
                    window.open('/', '_blank');
                }
            }, 2000);
        } else {
            // Affichage de l'erreur
            resultDiv.innerHTML = `
                <div class="alert alert-danger small">
                    <i class="bi bi-exclamation-triangle me-2"></i>
                    <strong>Erreur :</strong> ${result.error}
                </div>
            `;
            showToast('❌ Erreur génération slides', 'error');
        }
    } catch (error) {
        console.error('Erreur génération slides démo:', error);
        resultDiv.innerHTML = `
            <div class="alert alert-danger small">
                <i class="bi bi-exclamation-triangle me-2"></i>
                <strong>Erreur de connexion :</strong><br>
                ${error.message || 'Impossible de contacter le serveur'}
            </div>
        `;
        showToast('❌ Erreur de connexion', 'error');
    } finally {
        // Réactiver le bouton
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-play-fill me-1"></i> Générer slides de test';
        resultDiv.style.display = 'block';
    }
}
