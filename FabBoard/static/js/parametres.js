// FabBoard — Paramètres (Phase 2)

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

function setupEventListeners() {
    document.getElementById('form-params-general').addEventListener('submit', (e) => {
        e.preventDefault();
        saveParametres();
    });

    document.getElementById('btn-save-source').addEventListener('click', saveSource);
    document.getElementById('btn-add-source').addEventListener('click', openCreateSourceModal);
    document.getElementById('btn-refresh-sources').addEventListener('click', loadSources);
    document.getElementById('source-type').addEventListener('change', onSourceTypeChange);
    document.getElementById('btn-test-source-modal').addEventListener('click', testSourceFromModal);
    document.getElementById('sources-table-body').addEventListener('click', onSourcesTableClick);
    document.getElementById('modalSource').addEventListener('hidden.bs.modal', openCreateSourceModal);
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
    } catch (error) {
        console.error('Erreur chargement paramètres:', error);
    }
}

async function saveParametres() {
    const params = {
        fablab_name: document.getElementById('param-fablab-name').value.trim(),
        refresh_interval: document.getElementById('param-refresh').value,
        theme: document.getElementById('param-theme').value,
        police_dashboard: (document.getElementById('param-police')?.value
            || document.getElementById('param-font-family')?.value
            || 'inter'),
    };

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
        ]);

        applyFontFamily(params.police_dashboard);
        showToast('Paramètres enregistrés', 'success');
    } catch (error) {
        showToast(`Erreur sauvegarde: ${error.message}`, 'error');
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
