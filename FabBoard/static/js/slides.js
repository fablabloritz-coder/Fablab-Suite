/**
 * SLIDES.JS — Gestion de l'interface d'administration des slides
 * Phase 1.5 : Système configurable de slides
 */

// ========== HELPERS ==========
/**
 * Échappe le HTML pour éviter les injections XSS
 * Fallback si utils.js n'est pas chargé
 */
function safeEscape(str) {
    if (typeof escapeHtml !== 'undefined') {
        return escapeHtml(str);
    }
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

/**
 * Upload une image via /api/upload et met à jour le champ caché correspondant.
 */
function uploadImageField(fileInput, configKey) {
    const file = fileInput.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    fetch('/api/upload', { method: 'POST', body: formData })
        .then(r => r.json())
        .then(result => {
            if (result.success) {
                const hidden = fileInput.closest('.mb-3').querySelector('[data-config-key="' + configKey + '"]');
                if (hidden) hidden.value = result.url;
                // Mettre à jour l'aperçu
                let preview = fileInput.closest('.mb-3').querySelector('img');
                if (!preview) {
                    const div = document.createElement('div');
                    div.className = 'mb-2';
                    div.innerHTML = '<img style="max-width:100%;max-height:120px;border-radius:6px;border:1px solid #dee2e6;">';
                    fileInput.closest('.mb-3').insertBefore(div, fileInput.closest('.mb-3').querySelector('label').nextSibling);
                    preview = div.querySelector('img');
                }
                preview.src = result.url;
                showToast('Image uploadée', 'success');
            } else {
                showToast(result.error || 'Erreur upload', 'error');
            }
        })
        .catch(() => showToast('Erreur lors de l\'upload', 'error'));
}

/**
 * Upload une vidéo via /api/upload-video et met à jour le champ caché correspondant.
 */
function uploadVideoField(fileInput, configKey) {
    const file = fileInput.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    showToast('Upload en cours...', 'info');

    fetch('/api/upload-video', { method: 'POST', body: formData })
        .then(r => r.json())
        .then(result => {
            if (result.success) {
                const hidden = fileInput.closest('.mb-3').querySelector('[data-config-key="' + configKey + '"]');
                if (hidden) hidden.value = result.url;
                // Mettre à jour l'indication textuelle
                const textInput = fileInput.closest('.mb-3').querySelector('input[type="text"]');
                if (textInput) textInput.value = result.url;
                showToast('Vidéo uploadée', 'success');
            } else {
                showToast(result.error || 'Erreur upload', 'error');
            }
        })
        .catch(() => showToast('Erreur lors de l\'upload vidéo', 'error'));
}

/**
 * Sélectionne un média déjà uploadé et met à jour les champs de config.
 */
function selectExistingMedia(selectEl, configKey, mediaType) {
    const selectedUrl = selectEl.value || '';
    if (!selectedUrl) return;

    const container = selectEl.closest('.mb-3');
    if (!container) return;

    const hidden = container.querySelector('[data-config-key="' + configKey + '"]');
    if (hidden) hidden.value = selectedUrl;

    if (mediaType === 'image') {
        let preview = container.querySelector('img');
        if (!preview) {
            const div = document.createElement('div');
            div.className = 'mb-2';
            div.innerHTML = '<img style="max-width:100%;max-height:120px;border-radius:6px;border:1px solid #dee2e6;">';
            const firstInput = container.querySelector('input[type="hidden"]');
            if (firstInput) {
                container.insertBefore(div, firstInput.nextSibling);
            } else {
                container.appendChild(div);
            }
            preview = div.querySelector('img');
        }
        preview.src = selectedUrl;
    }

    if (mediaType === 'video') {
        const textInput = container.querySelector('input[type="text"]');
        if (textInput) textInput.value = selectedUrl;
    }

    showToast('Média sélectionné', 'success');
}

/**
 * Affiche/masque les options de fond selon le type sélectionné.
 */
function toggleFondOptions() {
    const type = document.getElementById('slideFondType').value;
    document.getElementById('fondCouleurGroup').style.display = type === 'couleur' ? '' : 'none';
    document.getElementById('fondImageGroup').style.display = type === 'image' ? '' : 'none';
}

/**
 * Upload d'image de fond de slide.
 */
function uploadSlideBg(fileInput) {
    const file = fileInput.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    fetch('/api/upload', { method: 'POST', body: formData })
        .then(r => r.json())
        .then(result => {
            if (result.success) {
                document.getElementById('slideFondImage').value = result.url;
                document.getElementById('fondImagePreview').innerHTML =
                    '<img src="' + escapeHtml(result.url) + '" style="max-width:100%;max-height:80px;border-radius:6px;">';
                showToast('Image de fond uploadée', 'success');
            } else {
                showToast(result.error || 'Erreur upload', 'error');
            }
        })
        .catch(() => showToast('Erreur lors de l\'upload', 'error'));
}

// ========== STATE ==========
let currentSlides = [];
let currentLayouts = [];
let currentWidgets = [];
let selectedSlideId = null;
let sortableInstance = null;
let isRenderingSlides = false; // Flag pour éviter les rendus concurrents
let currentEditingWidgetConfig = null; // Contexte d'édition config avancée

// ========== INIT ==========
document.addEventListener('DOMContentLoaded', () => {
    console.log('[SLIDES] DOM Content Loaded');
    try {
        setupEventListeners();
        loadInitialData();
        
        // Réinitialiser le mode config avancée quand le modal se ferme
        const wcModal = document.getElementById('widgetConfigModal');
        if (wcModal) {
            wcModal.addEventListener('hidden.bs.modal', function() {
                currentEditingWidgetConfig = null;
            });
        }
    } catch (error) {
        console.error('[SLIDES] Erreur fatale lors de l\'initialisation:', error);
        const container = document.getElementById('slidesList');
        if (container) {
            container.innerHTML = '<div class="alert alert-danger m-2">Erreur critique d\'initialisation. Vérifiez la console.</div>';
        }
    }
});

async function loadInitialData() {
    try {
        console.log('[SLIDES] Chargement des données...');
        
        // Charger toutes les données en parallèle
        const [slidesRes, layoutsRes, widgetsRes] = await Promise.all([
            apiCall('/api/slides?include_inactive=true'),
            apiCall('/api/layouts'),
            apiCall('/api/widgets')
        ]);
        
        console.log('[SLIDES] Réponses reçues API:', {
            slides: slidesRes,
            layouts: layoutsRes,
            widgets: widgetsRes
        });
        
        // Validation des réponses
        if (!slidesRes || !slidesRes.data) {
            throw new Error('Réponse invalide pour /api/slides');
        }
        if (!layoutsRes || !layoutsRes.data) {
            throw new Error('Réponse invalide pour /api/layouts');
        }
        if (!widgetsRes || !widgetsRes.data) {
            throw new Error('Réponse invalide pour /api/widgets');
        }
        
        currentSlides = Array.isArray(slidesRes.data) ? slidesRes.data : [];
        currentLayouts = Array.isArray(layoutsRes.data) ? layoutsRes.data : [];
        currentWidgets = Array.isArray(widgetsRes.data) ? widgetsRes.data : [];
        
        console.log('[SLIDES] Données chargées avec succès:', {
            slides: currentSlides.length,
            layouts: currentLayouts.length,
            widgets: currentWidgets.length
        });
        
        // Vérifier que currentSlides est un array avant le rendu
        if (!Array.isArray(currentSlides)) {
            console.error('[SLIDES] currentSlides n\'est pas un array:', currentSlides);
            currentSlides = [];
        }
        
        renderSlidesList(); // renderSlidesList appelle initSortable() à la fin
        
        // Sélectionner la première slide par défaut
        if (currentSlides.length > 0) {
            selectSlide(currentSlides[0].id);
            console.log('[SLIDES] Slide sélectionnée:', currentSlides[0].id);
        } else {
            console.warn('[SLIDES] Aucune slide disponible');
        }
        
    } catch (error) {
        console.error('[SLIDES] Erreur lors du chargement:', error);
        showToast('Erreur lors du chargement des données', 'error');
    }
}

function setupEventListeners() {
    // Boutons principaux - avec protection contre les éléments manquants
    const buttons = {
        'btnAddSlide': openAddSlideModal,
        'btnSaveSlide': saveSlide,
        'btnDeleteSlide': deleteCurrentSlide,
        'btnDeleteAllSlides': deleteAllSlides,
        'btnRefreshPreview': refreshPreview,
        'btnFullscreenPreview': openFullscreenPreview,
        'btnSaveWidgetConfig': saveWidgetConfig
    };
    
    for (const [btnId, handler] of Object.entries(buttons)) {
        const btn = document.getElementById(btnId);
        if (btn) {
            btn.addEventListener('click', handler);
        } else {
            console.warn(`[SLIDES] Bouton ${btnId} non trouvé dans le DOM`);
        }
    }
    
    console.log('[SLIDES] Event listeners configurés');
}

// ========== SLIDES LIST ==========
function renderSlidesList() {
    // Éviter les rendus concurrents
    if (isRenderingSlides) {
        console.warn('[SLIDES] Rendu déjà en cours, ignoré');
        return;
    }
    
    const container = document.getElementById('slidesList');
    if (!container) {
        console.error('[SLIDES] Container slidesList introuvable');
        return;
    }
    
    if (currentSlides.length === 0) {
        container.innerHTML = `
            <div class="text-center text-muted p-4">
                <i class="bi bi-inbox"></i>
                <p>Aucune slide créée</p>
            </div>
        `;
        return;
    }
    
    isRenderingSlides = true;
    
    try {
        const items = currentSlides.map((slide, index) => {
            try {
                if (!slide || typeof slide !== 'object') {
                    console.warn('[SLIDES] Slide invalide a l\'index ' + index + ':', slide);
                    return '';
                }
                
                const isActive = slide.id === selectedSlideId;
                const isInactive = !slide.actif;
                const nom = safeEscape(slide.nom || 'Sans nom');
                const layoutNom = slide.layout_nom || 'Gabarit';
                const temps = slide.temps_affichage || 30;
                const ordre = slide.ordre || '';
                
                return `
                    <div class="slide-item ${isInactive ? 'inactive' : ''} ${isActive ? 'active' : ''}" 
                         data-slide-id="${slide.id}"
                         onclick="selectSlide(${slide.id})">
                        <div class="slide-item-handle">
                            <i class="bi bi-grip-vertical"></i>
                        </div>
                        <div class="slide-item-order">${ordre}</div>
                        <div class="slide-item-content">
                            <div class="slide-item-title">${nom}</div>
                            <div class="slide-item-meta">
                                <span><i class="bi bi-grid-3x2"></i> ${layoutNom}</span>
                                <span><i class="bi bi-clock"></i> ${temps}s</span>
                            </div>
                        </div>
                    </div>
                `;
            } catch (slideError) {
                console.error(`[SLIDES] Erreur rendering slide index ${index}:`, slideError, slide);
                return '';
            }
        });
        
        const htmlContent = items.filter(item => item).join('');
        container.innerHTML = htmlContent || '<div class="text-center text-muted p-4"><i class="bi bi-inbox"></i><p>Aucune slide valide</p></div>';
        
        console.log('[SLIDES] Liste rendue:', currentSlides.length, 'slides');
        
        // Réinitialiser SortableJS après que le DOM soit complètement rendu
        // Utiliser requestAnimationFrame pour s'assurer que innerHTML a fini de s'appliquer
        requestAnimationFrame(() => {
            if (sortableInstance) {
                try {
                    sortableInstance.destroy();
                    sortableInstance = null;
                } catch (e) {
                    console.warn('[SLIDES] Erreur destruction Sortable:', e);
                    sortableInstance = null;
                }
            }
            initSortable();
            // Libérer le flag après l'initialisation de Sortable
            isRenderingSlides = false;
        });
    } catch (e) {
        isRenderingSlides = false; // Libérer le flag en cas d'erreur
        console.error('[SLIDES] Erreur renderSlidesList:', e, 'Stack:', e.stack);
        console.error('[SLIDES] currentSlides:', currentSlides);
        container.innerHTML = '<div class="alert alert-danger m-2"><strong>Erreur d\'affichage des slides:</strong><br/>' + e.message + '</div>';
    }
}

function initSortable() {
    const container = document.getElementById('slidesList');
    
    // Vérifications de sécurité
    if (!container) {
        console.warn('[SLIDES] Container slidesList introuvable pour Sortable');
        return;
    }
    
    if (currentSlides.length === 0) {
        console.log('[SLIDES] Aucune slide, Sortable non initialisé');
        return;
    }
    
    // Vérifier que le conteneur a effectivement des éléments .slide-item
    const slideItems = container.querySelectorAll('.slide-item');
    if (slideItems.length === 0) {
        console.warn('[SLIDES] Aucun élément .slide-item trouvé dans le conteneur');
        return;
    }
    
    // Vérifier que SortableJS est chargé
    if (typeof Sortable === 'undefined') {
        console.warn('[SLIDES] SortableJS n\'est pas chargé. Drag/drop désactivé. Vérifiez le CDN jsdelivr.net');
        return;
    }
    
    // S'assurer qu'il n'y a pas déjà une instance
    if (sortableInstance) {
        try {
            sortableInstance.destroy();
            sortableInstance = null;
        } catch (e) {
            console.warn('[SLIDES] Erreur destruction instance Sortable existante:', e);
            sortableInstance = null;
        }
    }
    
    try {
        sortableInstance = Sortable.create(container, {
            animation: 200,
            handle: '.slide-item-handle',
            ghostClass: 'sortable-ghost',
            draggable: '.slide-item',
            onEnd: handleReorder
        });
        console.log('[SLIDES] SortableJS initialisé avec succès sur', slideItems.length, 'slides');
    } catch (error) {
        console.error('[SLIDES] Erreur lors de l\'initialisation de Sortable:', error);
        sortableInstance = null;
    }
}

async function handleReorder(evt) {
    const newOrder = Array.from(evt.to.children).map(el => {
        return parseInt(el.getAttribute('data-slide-id'));
    });
    
    try {
        await apiCall('/api/slides/reorder', 'PATCH', { order: newOrder });
        
        // Mettre à jour l'ordre localement
        currentSlides.sort((a, b) => {
            return newOrder.indexOf(a.id) - newOrder.indexOf(b.id);
        });
        currentSlides.forEach((slide, index) => {
            slide.ordre = index + 1;
        });
        
        renderSlidesList();
        showToast('Ordre des slides mis à jour', 'success');
    } catch (error) {
        showToast('Erreur lors de la réorganisation', 'error');
        renderSlidesList(); // Revenir à l'état précédent
    }
}

// ========== SLIDE SELECTION ==========
function selectSlide(slideId) {
    console.log('[SLIDES] Sélection de la slide:', slideId);
    selectedSlideId = slideId;
    renderSlidesList();
    
    try {
        renderPreview();
        console.log('[SLIDES] Aperçu rendu');
    } catch (e) {
        console.error('[SLIDES] Erreur renderPreview:', e);
    }
    
    try {
        renderConfig();
        console.log('[SLIDES] Configuration rendue');
    } catch (e) {
        console.error('[SLIDES] Erreur renderConfig:', e);
    }
    
    // Afficher le bouton de suppression
    const btnDelete = document.getElementById('btnDeleteSlide');
    if (btnDelete) {
       btnDelete.style.display = 'block';
    }
}

// ========== APERÇU ==========
function renderPreview() {
    const slide = currentSlides.find(s => s.id === selectedSlideId);
    if (!slide) return;
    
    const container = document.getElementById('previewContainer');
    const layout = JSON.parse(slide.grille_json);
    const slideWidgets = slide.widgets || [];
    
    // Construire la grille CSS
    const gridStyle = `
        grid-template-columns: repeat(${slide.colonnes}, 1fr);
        grid-template-rows: repeat(${slide.lignes}, 1fr);
    `;
    
    // Construire les widgets
    const widgetsHTML = layout.map((pos, index) => {
        const widget = slideWidgets.find(w => w.position === index);
        
        const style = `
            grid-column: ${pos.x + 1} / span ${pos.w};
            grid-row: ${pos.y + 1} / span ${pos.h};
        `;
        
        if (widget) {
            return `
                <div class="preview-widget" style="${style}">
                    <div class="preview-widget-icon">${widget.icone || '📦'}</div>
                    <div class="preview-widget-name">${widget.widget_nom || 'Widget'}</div>
                </div>
            `;
        } else {
            return `
                <div class="preview-widget preview-widget-empty" style="${style}">
                    <div class="preview-widget-icon">📦</div>
                    <div class="preview-widget-name">Position ${index + 1}</div>
                </div>
            `;
        }
    }).join('');
    
    container.innerHTML = `
        <div class="preview-grid" style="${gridStyle}">
            ${widgetsHTML}
        </div>
    `;
    
    // Mettre à jour les infos
    document.getElementById('previewLayoutName').textContent = slide.layout_nom || 'Gabarit personnalisé';
    document.getElementById('previewDuration').textContent = `Durée : ${slide.temps_affichage}s`;
}

function refreshPreview() {
    renderPreview();
    showToast('Aperçu actualisé', 'info');
}

function openFullscreenPreview() {
    // Ouvrir le dashboard dans un nouvel onglet
    window.open('/', '_blank');
}

// ========== CONFIGURATION ==========
function renderConfig() {
    const container = document.getElementById('configContainer');
    if (!container) {
        console.error('[SLIDES] Container configContainer introuvable');
        return;
    }
    
    const slide = currentSlides.find(s => s.id === selectedSlideId);
    if (!slide) {
        container.innerHTML = `
            <div class="empty-message">
                <p>Sélectionnez une slide pour la configurer</p>
            </div>
        `;
        return;
    }
    
    try {
        const layout = JSON.parse(slide.grille_json || '[]');
        const slideWidgets = slide.widgets || [];
        
        container.innerHTML = `
            <!-- Infos générales -->
            <div class="config-section">
                <div class="config-section-title">
                    <i class="bi bi-info-circle"></i> Informations
                </div>
                <div class="mb-2">
                    <strong>Nom :</strong> ${safeEscape(slide.nom || 'Sans nom')}
                </div>
                <div class="mb-2">
                    <strong>Layout :</strong> ${slide.layout_nom || 'Gabarit personnalisé'}
                </div>
                <div class="mb-2">
                    <strong>Durée :</strong> ${slide.temps_affichage || 30} secondes
                </div>
                <div class="mb-2">
                    <strong>Statut :</strong>
                    <span class="badge ${slide.actif ? 'bg-success' : 'bg-secondary'}">
                        ${slide.actif ? 'Active' : 'Inactive'}
                    </span>
                </div>
                <button class="btn btn-sm btn-outline-primary w-100" onclick="editSlide(${slide.id})">
                    <i class="bi bi-pencil"></i> Modifier
                </button>
            </div>
            
            <!-- Widgets -->
            <div class="config-section">
                <div class="config-section-title">
                    <i class="bi bi-grid-3x3"></i> Widgets (${slideWidgets.length}/${layout.length})
                </div>
                <div class="widget-position-grid">
                    ${layout.map((pos, index) => renderWidgetPosition(slide, index)).join('')}
                </div>
            </div>
        `;
        
        console.log('[SLIDES] Configuration rendue pour slide', slide.id);
    } catch (e) {
        console.error('[SLIDES] Erreur renderConfig:', e);
        container.innerHTML = '<div class="alert alert-danger">Erreur d\'affichage de la configuration</div>';
    }
}

function renderWidgetPosition(slide, position) {
    const slideWidgets = slide.widgets || [];
    const widget = slideWidgets.find(w => w.position === position);
    
    return `
        <div class="widget-position-item" onclick="selectWidgetForPosition(${slide.id}, ${position})">
            <div class="widget-position-header">
                <span class="widget-position-label">Position ${position + 1}</span>
                ${widget ? `
                    <div class="widget-position-actions">
                        <button class="btn btn-xs btn-outline-secondary" onclick="event.stopPropagation(); configureWidget(${slide.id}, ${position})">
                            <i class="bi bi-gear"></i>
                        </button>
                        <button class="btn btn-xs btn-outline-danger" onclick="event.stopPropagation(); removeWidget(${slide.id}, ${position})">
                            <i class="bi bi-x"></i>
                        </button>
                    </div>
                ` : ''}
            </div>
            <div class="widget-position-content">
                ${widget ? `
                    <div class="widget-position-icon">${widget.icone || '📦'}</div>
                    <div class="widget-position-name">${widget.widget_nom || 'Widget'}</div>
                ` : `
                    <div class="widget-position-icon">➕</div>
                    <div class="widget-position-name widget-position-empty">Cliquer pour ajouter</div>
                `}
            </div>
        </div>
    `;
}

// ========== MODAL : Ajouter/Modifier Slide ==========
function openAddSlideModal() {
    document.getElementById('slideModalTitle').textContent = 'Nouvelle Slide';
    document.getElementById('slideId').value = '';
    document.getElementById('slideName').value = '';
    document.getElementById('slideDuration').value = '30';
    document.getElementById('slideActive').checked = true;
    document.getElementById('slideFondType').value = 'defaut';
    document.getElementById('slideFondCouleur').value = '#0b1120';
    document.getElementById('slideFondImage').value = '';
    document.getElementById('fondImagePreview').innerHTML = '';
    toggleFondOptions();
    
    renderLayoutSelector();
    
    const modal = new bootstrap.Modal(document.getElementById('slideModal'));
    modal.show();
}

function editSlide(slideId) {
    const slide = currentSlides.find(s => s.id === slideId);
    if (!slide) return;
    
    document.getElementById('slideModalTitle').textContent = 'Modifier la Slide';
    document.getElementById('slideId').value = slide.id;
    document.getElementById('slideName').value = slide.nom;
    document.getElementById('slideDuration').value = slide.temps_affichage;
    document.getElementById('slideActive').checked = slide.actif === 1;

    // Fond de slide
    const fondType = slide.fond_type || 'defaut';
    const fondValeur = slide.fond_valeur || '';
    document.getElementById('slideFondType').value = fondType;
    if (fondType === 'couleur') {
        document.getElementById('slideFondCouleur').value = fondValeur || '#0b1120';
    } else if (fondType === 'image') {
        document.getElementById('slideFondImage').value = fondValeur;
        const preview = document.getElementById('fondImagePreview');
        preview.innerHTML = fondValeur ? '<img src="' + escapeHtml(fondValeur) + '" style="max-width:100%;max-height:80px;border-radius:6px;">' : '';
    }
    toggleFondOptions();
    
    renderLayoutSelector(slide.layout_id);
    
    const modal = new bootstrap.Modal(document.getElementById('slideModal'));
    modal.show();
}

function renderLayoutSelector(selectedLayoutId = null) {
    const container = document.getElementById('layoutSelector');
    
    container.innerHTML = currentLayouts.map(layout => {
        const grille = JSON.parse(layout.grille_json);
        const gridStyle = `
            grid-template-columns: repeat(${layout.colonnes}, 1fr);
            grid-template-rows: repeat(${layout.lignes}, 1fr);
        `;
        
        const cellsHTML = grille.map(pos => `
            <div class="layout-preview-cell" style="
                grid-column: ${pos.x + 1} / span ${pos.w};
                grid-row: ${pos.y + 1} / span ${pos.h};
            "></div>
        `).join('');
        
        return `
            <div class="layout-option ${layout.id === selectedLayoutId ? 'selected' : ''}" 
                 data-layout-id="${layout.id}"
                 onclick="selectLayout(${layout.id})">
                <div class="layout-preview" style="${gridStyle}">
                    ${cellsHTML}
                </div>
                <div class="layout-name">${layout.nom}</div>
            </div>
        `;
    }).join('');
}

function selectLayout(layoutId) {
    // Retirer la sélection précédente
    document.querySelectorAll('.layout-option').forEach(el => {
        el.classList.remove('selected');
    });
    
    // Ajouter la sélection
    document.querySelector(`[data-layout-id="${layoutId}"]`).classList.add('selected');
}

async function saveSlide() {
    const slideId = document.getElementById('slideId').value;
    const nom = document.getElementById('slideName').value.trim();
    const temps_affichage = parseInt(document.getElementById('slideDuration').value);
    const actif = document.getElementById('slideActive').checked ? 1 : 0;
    
    // Fond
    const fond_type = document.getElementById('slideFondType').value;
    let fond_valeur = '';
    if (fond_type === 'couleur') {
        fond_valeur = document.getElementById('slideFondCouleur').value;
    } else if (fond_type === 'image') {
        fond_valeur = document.getElementById('slideFondImage').value;
    }
    
    // Récupérer le layout sélectionné
    const selectedLayout = document.querySelector('.layout-option.selected');
    if (!selectedLayout) {
        showToast('Veuillez sélectionner un gabarit', 'warning');
        return;
    }
    
    const layout_id = parseInt(selectedLayout.getAttribute('data-layout-id'));
    
    if (!nom) {
        showToast('Le nom est requis', 'warning');
        return;
    }
    
    try {
        const data = { nom, layout_id, temps_affichage, actif, fond_type, fond_valeur };
        let result;
        let newSlideId;
        
        if (slideId) {
            // Modification — si le layout change, nettoyer les widgets hors bornes
            const slide = currentSlides.find(s => s.id == slideId);
            if (slide && slide.layout_id !== layout_id) {
                const newLayout = currentLayouts.find(l => l.id === layout_id);
                if (newLayout) {
                    const maxPositions = JSON.parse(newLayout.grille_json).length;
                    const validWidgets = (slide.widgets || [])
                        .filter(w => w.position < maxPositions)
                        .map(w => ({
                            widget_id: w.widget_id,
                            position: w.position,
                            config: JSON.parse(w.config_json || '{}')
                        }));
                    data.widgets = validWidgets;
                }
            }
            result = await apiCall(`/api/slides/${slideId}`, 'PUT', data);
            const index = currentSlides.findIndex(s => s.id == slideId);
            currentSlides[index] = result.data;
            newSlideId = slideId;
            showToast('Slide modifiée avec succès', 'success');
        } else {
            // Création
            result = await apiCall('/api/slides', 'POST', data);
            currentSlides.push(result.data);
            newSlideId = result.data.id;
            showToast('Slide créée avec succès', 'success');
        }
        
        renderSlidesList();
        selectSlide(parseInt(newSlideId));
        
        // Fermer le modal
        bootstrap.Modal.getInstance(document.getElementById('slideModal')).hide();
        
    } catch (error) {
        showToast('Erreur lors de l\'enregistrement', 'error');
        console.error(error);
    }
}

async function deleteCurrentSlide() {
    if (!selectedSlideId) return;
    
    if (!confirm('Voulez-vous vraiment supprimer cette slide ?')) {
        return;
    }
    
    try {
        await apiCall(`/api/slides/${selectedSlideId}`, 'DELETE');
        
        currentSlides = currentSlides.filter(s => s.id !== selectedSlideId);
        selectedSlideId = null;
        
        renderSlidesList();
        document.getElementById('configContainer').innerHTML = `
            <div class="config-placeholder">
                <i class="bi bi-info-circle"></i>
                <p>Slide supprimée</p>
            </div>
        `;
        document.getElementById('previewContainer').innerHTML = `
            <div class="preview-placeholder">
                <i class="bi bi-tv-fill"></i>
                <p>Sélectionnez une slide</p>
            </div>
        `;
        document.getElementById('btnDeleteSlide').style.display = 'none';
        
        showToast('Slide supprimée', 'success');
        
        // Sélectionner une autre slide si disponible
        if (currentSlides.length > 0) {
            selectSlide(currentSlides[0].id);
        }
        
    } catch (error) {
        showToast('Erreur lors de la suppression', 'error');
        console.error(error);
    }
}

async function deleteAllSlides() {
    if (currentSlides.length === 0) {
        showToast('Aucune slide à supprimer', 'info');
        return;
    }

    if (!confirm(`Voulez-vous vraiment supprimer les ${currentSlides.length} slide(s) ? Cette action est irréversible.`)) {
        return;
    }

    try {
        await apiCall('/api/slides/all', 'DELETE');

        currentSlides = [];
        selectedSlideId = null;

        renderSlidesList();
        document.getElementById('configContainer').innerHTML = `
            <div class="config-placeholder">
                <i class="bi bi-info-circle"></i>
                <p>Toutes les slides ont été supprimées</p>
            </div>
        `;
        document.getElementById('previewContainer').innerHTML = `
            <div class="preview-placeholder">
                <i class="bi bi-tv-fill"></i>
                <p>Sélectionnez une slide</p>
            </div>
        `;
        document.getElementById('btnDeleteSlide').style.display = 'none';

        showToast('Toutes les slides ont été supprimées', 'success');
    } catch (error) {
        showToast('Erreur lors de la suppression', 'error');
        console.error(error);
    }
}

// ========== GESTION DES WIDGETS ==========
let currentEditingPosition = null;

function selectWidgetForPosition(slideId, position) {
    currentEditingPosition = { slideId, position };
    
    const container = document.getElementById('widgetConfigForm');
    
    container.innerHTML = `
        <div class="mb-3">
            <label class="form-label">Choisir un widget</label>
            <div class="widget-selector">
                ${currentWidgets.map(widget => `
                    <div class="widget-option" data-widget-id="${widget.id}" onclick="selectWidget(${widget.id})">
                        <div class="widget-option-icon">${widget.icone}</div>
                        <div class="widget-option-name">${widget.nom}</div>
                    </div>
                `).join('')}
            </div>
        </div>
    `;
    
    const modal = new bootstrap.Modal(document.getElementById('widgetConfigModal'));
    modal.show();
}

function selectWidget(widgetId) {
    document.querySelectorAll('.widget-option').forEach(el => el.classList.remove('selected'));
    document.querySelector(`[data-widget-id="${widgetId}"]`).classList.add('selected');
}

async function saveWidgetConfig() {
    // Mode configuration avancée
    if (currentEditingWidgetConfig) {
        return saveWidgetAdvancedConfig();
    }
    
    // Mode sélection de widget
    if (!currentEditingPosition) return;
    
    const selectedWidget = document.querySelector('.widget-option.selected');
    if (!selectedWidget) {
        showToast('Veuillez sélectionner un widget', 'warning');
        return;
    }
    
    const widgetId = parseInt(selectedWidget.getAttribute('data-widget-id'));
    const { slideId, position } = currentEditingPosition;
    
    try {
        // Récupérer la slide actuelle
        const slide = currentSlides.find(s => s.id === slideId);
        
        // Mettre à jour ou ajouter le widget
        const existingWidgetIndex = slide.widgets.findIndex(w => w.position === position);
        if (existingWidgetIndex >= 0) {
            slide.widgets[existingWidgetIndex] = {
                ...slide.widgets[existingWidgetIndex],
                widget_id: widgetId,
                position: position
            };
        } else {
            slide.widgets.push({
                slide_id: slideId,
                widget_id: widgetId,
                position: position,
                config_json: '{}'
            });
        }
        
        // Sauvegarder via l'API
        await apiCall(`/api/slides/${slideId}`, 'PUT', {
            nom: slide.nom,
            layout_id: slide.layout_id,
            temps_affichage: slide.temps_affichage,
            actif: slide.actif,
            widgets: slide.widgets.map(w => ({
                widget_id: w.widget_id,
                position: w.position,
                config: JSON.parse(w.config_json || '{}')
            }))
        });
        
        // Rafraîchir l'affichage
        const updatedSlide = await apiCall(`/api/slides/${slideId}`);
        const index = currentSlides.findIndex(s => s.id === slideId);
        currentSlides[index] = updatedSlide.data;
        
        renderPreview();
        renderConfig();
        
        bootstrap.Modal.getInstance(document.getElementById('widgetConfigModal')).hide();
        showToast('Widget ajouté avec succès', 'success');
        
    } catch (error) {
        showToast('Erreur lors de l\'ajout du widget', 'error');
        console.error(error);
    }
}

// ========== CONFIGURATION AVANCÉE DES WIDGETS ==========

/**
 * Définitions des options de configuration par type de widget
 */
const ECHELLE_FIELD = {
    key: 'echelle', label: "Échelle d'affichage (TV)", type: 'select', options: [
        { value: '1', label: '1× — Normal' },
        { value: '1.25', label: '1.25× — Légèrement agrandi' },
        { value: '1.5', label: '1.5× — Grand' },
        { value: '2', label: '2× — Très grand' },
        { value: '2.5', label: '2.5× — Extra grand' },
        { value: '3', label: '3× — Très grand' },
        { value: '4', label: '4× — Maximal' }
    ], default: '1'
};

const WIDGET_CONFIG_DEFINITIONS = {
    horloge: {
        titre: 'Horloge',
        fields: [
            { key: 'format', label: 'Format horaire', type: 'select', options: [
                { value: '24h', label: '24 heures' },
                { value: '12h', label: '12 heures (AM/PM)' }
            ], default: '24h' },
            { key: 'afficher_secondes', label: 'Afficher les secondes', type: 'checkbox', default: true },
            { key: 'afficher_date', label: 'Afficher la date', type: 'checkbox', default: true },
            ECHELLE_FIELD
        ]
    },
    texte_libre: {
        titre: 'Texte libre',
        fields: [
            { key: 'titre', label: 'Titre', type: 'text', default: 'Information', placeholder: 'Titre du bloc' },
            { key: 'contenu', label: 'Contenu', type: 'textarea', default: '', placeholder: 'Texte à afficher...' },
            { key: 'taille_texte', label: 'Taille du texte', type: 'select', options: [
                { value: 'small', label: 'Petit' },
                { value: 'normal', label: 'Normal' },
                { value: 'large', label: 'Grand' },
                { value: 'xlarge', label: 'Très grand' }
            ], default: 'normal' },
            { key: 'alignement', label: 'Alignement', type: 'select', options: [
                { value: 'left', label: 'Gauche' },
                { value: 'center', label: 'Centré' },
                { value: 'right', label: 'Droite' }
            ], default: 'left' },
            ECHELLE_FIELD
        ]
    },
    compteurs: {
        titre: 'Compteurs Fabtrack',
        fields: [
            { key: 'source_id', label: 'Source Fabtrack', type: 'source_select', source_type: 'fabtrack', default: '' },
            { key: 'afficher_en_attente', label: 'Afficher "À faire"', type: 'checkbox', default: true },
            { key: 'afficher_en_cours', label: 'Afficher "En cours"', type: 'checkbox', default: true },
            { key: 'afficher_termines', label: 'Afficher "Terminées"', type: 'checkbox', default: true },
            ECHELLE_FIELD
        ]
    },
    activites: {
        titre: 'Activités Fabtrack',
        fields: [
            { key: 'source_id', label: 'Source Fabtrack', type: 'source_select', source_type: 'fabtrack', default: '' },
            { key: 'nombre_max', label: "Nombre max d'activités", type: 'number', default: 5, min: 1, max: 20 },
            { key: 'filtre_urgence', label: 'Filtrer par urgence', type: 'select', options: [
                { value: '', label: 'Toutes' },
                { value: 'haute', label: 'Haute uniquement' },
                { value: 'moyenne', label: 'Moyenne et +' },
                { value: 'basse', label: 'Basse et +' }
            ], default: '' },
            ECHELLE_FIELD
        ]
    },
    calendrier: {
        titre: 'Événements calendrier',
        fields: [
            { key: 'source_id', label: 'Source CalDAV', type: 'source_select', source_type: 'nextcloud_caldav', default: '' },
            { key: 'nombre_max', label: "Nombre max d'événements", type: 'number', default: 10, min: 1, max: 30 },
            { key: 'jours_avance', label: "Jours à l'avance", type: 'number', default: 7, min: 1, max: 60 },
            { key: 'taille_texte', label: 'Taille du texte', type: 'select', options: [
                { value: 'small', label: 'Petit' },
                { value: 'normal', label: 'Normal' },
                { value: 'large', label: 'Grand' },
                { value: 'xlarge', label: 'Très grand' }
            ], default: 'normal' },
            { key: 'taille_cartes', label: 'Taille des cartes', type: 'select', options: [
                { value: 'compact', label: 'Compact' },
                { value: 'normal', label: 'Normal' },
                { value: 'large', label: 'Grand' },
                { value: 'xlarge', label: 'Très grand' }
            ], default: 'normal' },
            { key: 'afficher_date_dessus', label: 'Afficher la date au-dessus', type: 'checkbox', default: true },
            { key: 'code_couleur_urgence', label: 'Code couleur urgence', type: 'checkbox', default: true },
            ECHELLE_FIELD
        ]
    },
    meteo: {
        titre: 'Météo',
        fields: [
            { key: 'source_id', label: 'Source OpenWeatherMap', type: 'source_select', source_type: 'openweathermap', default: '' },
            { key: 'ville', label: 'Ville (override)', type: 'text', default: '', placeholder: 'Ex: Nancy, FR' },
            { key: 'unite', label: 'Unité', type: 'select', options: [
                { value: 'celsius', label: 'Celsius (°C)' },
                { value: 'fahrenheit', label: 'Fahrenheit (°F)' }
            ], default: 'celsius' },
            ECHELLE_FIELD
        ]
    },
    fabtrack_stats: {
        titre: 'Stats Fabtrack',
        fields: [
            { key: 'source_id', label: 'Source Fabtrack', type: 'source_select', source_type: 'fabtrack', default: '' },
            { key: 'periode', label: 'Période', type: 'select', options: [
                { value: 'jour', label: "Aujourd'hui" },
                { value: 'semaine', label: 'Cette semaine' },
                { value: 'mois', label: 'Ce mois' }
            ], default: 'jour' },
            ECHELLE_FIELD
        ]
    },
    fabtrack_conso: {
        titre: 'Consommations Fabtrack',
        fields: [
            { key: 'source_id', label: 'Source Fabtrack', type: 'source_select', source_type: 'fabtrack', default: '' },
            { key: 'nombre_max', label: 'Nombre max de consommations', type: 'number', default: 10, min: 1, max: 100 },
            ECHELLE_FIELD
        ]
    },
    fabtrack_machines: {
        titre: 'État machines',
        fields: [
            { key: 'source_id', label: 'Source Fabtrack', type: 'source_select', source_type: 'fabtrack', default: '' },
            { key: 'afficher_inactives', label: 'Afficher les machines inactives', type: 'checkbox', default: false },
            ECHELLE_FIELD
        ]
    },
    fabtrack_missions: {
        titre: 'Missions Fabtrack',
        fields: [
            { key: 'source_id', label: 'Source Fabtrack', type: 'source_select', source_type: 'fabtrack', default: '' },
            { key: 'nombre_max', label: 'Nombre max par colonne', type: 'number', default: 10, min: 1, max: 50 },
            { key: 'afficher_a_faire', label: 'Afficher "À faire"', type: 'checkbox', default: true },
            { key: 'afficher_en_cours', label: 'Afficher "En cours"', type: 'checkbox', default: true },
            { key: 'afficher_termine', label: 'Afficher "Terminé"', type: 'checkbox', default: false },
            ECHELLE_FIELD
        ]
    },
    imprimantes: {
        titre: 'Imprimantes 3D',
        fields: [
            { key: 'source_id', label: 'Source imprimantes', type: 'source_select', source_type: 'repetier', default: '' },
            { key: 'afficher_inactives', label: 'Afficher les imprimantes hors-ligne', type: 'checkbox', default: false },
            ECHELLE_FIELD
        ]
    },
    image: {
        titre: 'Image',
        fields: [
            { key: 'image_url', label: 'Image', type: 'image_upload', default: '' },
            { key: 'titre_image', label: 'Titre (optionnel)', type: 'text', default: '', placeholder: 'Légende sous l\'image' },
            { key: 'object_fit', label: 'Ajustement', type: 'select', options: [
                { value: 'contain', label: 'Contenir (proportions gardées)' },
                { value: 'cover', label: 'Couvrir (recadré)' },
                { value: 'fill', label: 'Étirer' }
            ], default: 'contain' }
        ]
    },
    video: {
        titre: 'Vidéo',
        fields: [
            { key: 'video_type', label: 'Type de source', type: 'select', options: [
                { value: 'local', label: 'Vidéo locale (uploadée)' },
                { value: 'youtube', label: 'YouTube (ID de la vidéo)' },
                { value: 'dailymotion', label: 'Dailymotion (ID de la vidéo)' },
                { value: 'url', label: 'URL directe (mp4, webm)' }
            ], default: 'local' },
            { key: 'video_src', label: 'Source vidéo', type: 'video_upload', default: '',
              placeholder: 'Upload une vidéo ou saisissez l\'ID YouTube / Dailymotion' },
            { key: 'autoplay', label: 'Lecture automatique', type: 'checkbox', default: true },
            { key: 'boucle', label: 'Boucle', type: 'checkbox', default: true },
            { key: 'muet', label: 'Muet (obligatoire pour autoplay)', type: 'checkbox', default: true }
        ]
    },
    timer: {
        titre: 'Timer',
        fields: [
            { key: 'titre', label: 'Titre', type: 'text', default: 'Compte à rebours', placeholder: 'Ex: Portes ouvertes' },
            { key: 'date_cible', label: 'Date cible', type: 'date', default: '' },
            { key: 'heure_cible', label: 'Heure cible', type: 'time', default: '00:00' },
            { key: 'afficher_secondes', label: 'Afficher les secondes', type: 'checkbox', default: true },
            ECHELLE_FIELD
        ]
    },
    gif: {
        titre: 'GIF',
        fields: [
            { key: 'gif_type', label: 'Source du GIF', type: 'select', options: [
                { value: 'local', label: 'GIF local (uploadé)' },
                { value: 'url', label: 'URL directe' }
            ], default: 'local' },
            { key: 'gif_url', label: 'GIF local', type: 'image_upload', default: '' },
            { key: 'gif_direct_url', label: 'URL du GIF (.gif)', type: 'text', default: '', placeholder: 'https://media.tenor.com/.../tenor.gif' },
            { key: 'gif_object_fit', label: 'Ajustement', type: 'select', options: [
                { value: 'none', label: 'Taille d\'origine' },
                { value: 'contain', label: 'Centré (contenir)' },
                { value: 'cover', label: 'Couvrir' },
                { value: 'fill', label: 'Étendu (étirer)' },
                { value: 'scale-down', label: 'Réduire si nécessaire' }
            ], default: 'cover' },
            { key: 'gif_position', label: 'Position', type: 'select', options: [
                { value: 'center', label: 'Centre' },
                { value: 'top', label: 'Haut' },
                { value: 'bottom', label: 'Bas' },
                { value: 'left', label: 'Gauche' },
                { value: 'right', label: 'Droite' },
                { value: 'top left', label: 'Haut gauche' },
                { value: 'top right', label: 'Haut droite' },
                { value: 'bottom left', label: 'Bas gauche' },
                { value: 'bottom right', label: 'Bas droite' }
            ], default: 'center' },
            ECHELLE_FIELD
        ]
    },
    // Alias legacy conservés pour ouvrir la configuration même si un ancien code subsiste.
    fabtrack: {
        titre: 'Stats Fabtrack',
        fields: [
            { key: 'source_id', label: 'Source Fabtrack', type: 'source_select', source_type: 'fabtrack', default: '' },
            { key: 'periode', label: 'Période', type: 'select', options: [
                { value: 'jour', label: "Aujourd'hui" },
                { value: 'semaine', label: 'Cette semaine' },
                { value: 'mois', label: 'Ce mois' }
            ], default: 'jour' },
            ECHELLE_FIELD
        ]
    },
    graph_conso: {
        titre: 'Consommations Fabtrack',
        fields: [
            { key: 'source_id', label: 'Source Fabtrack', type: 'source_select', source_type: 'fabtrack', default: '' },
            { key: 'nombre_max', label: 'Nombre max de consommations', type: 'number', default: 10, min: 1, max: 100 },
            ECHELLE_FIELD
        ]
    },
    machines: {
        titre: 'État machines',
        fields: [
            { key: 'source_id', label: 'Source Fabtrack', type: 'source_select', source_type: 'fabtrack', default: '' },
            { key: 'afficher_inactives', label: 'Afficher les machines inactives', type: 'checkbox', default: false },
            ECHELLE_FIELD
        ]
    },
    missions: {
        titre: 'Missions Fabtrack',
        fields: [
            { key: 'source_id', label: 'Source Fabtrack', type: 'source_select', source_type: 'fabtrack', default: '' },
            { key: 'nombre_max', label: 'Nombre max par colonne', type: 'number', default: 10, min: 1, max: 50 },
            { key: 'afficher_a_faire', label: 'Afficher "À faire"', type: 'checkbox', default: true },
            { key: 'afficher_en_cours', label: 'Afficher "En cours"', type: 'checkbox', default: true },
            { key: 'afficher_termine', label: 'Afficher "Terminé"', type: 'checkbox', default: false },
            ECHELLE_FIELD
        ]
    }
};

function configureWidget(slideId, position) {
    const slide = currentSlides.find(s => s.id === slideId);
    if (!slide) return;
    
    const widget = slide.widgets.find(w => w.position === position);
    if (!widget) {
        showToast('Aucun widget à cette position', 'warning');
        return;
    }
    
    const widgetCode = widget.widget_code;
    const configDef = WIDGET_CONFIG_DEFINITIONS[widgetCode];
    
    if (!configDef || configDef.fields.length === 0) {
        showToast('Aucune option de configuration pour ce widget', 'info');
        return;
    }
    
    // Charger la config existante
    let currentConfig = {};
    try { currentConfig = JSON.parse(widget.config_json || '{}'); } catch(e) {}

    // Compatibilité : migrer l'ancienne config Tenor vers URL directe
    if (widgetCode === 'gif') {
        if (currentConfig.gif_type === 'tenor') {
            currentConfig.gif_type = 'url';
        }
        if (!currentConfig.gif_direct_url && currentConfig.tenor_gif_url) {
            currentConfig.gif_direct_url = currentConfig.tenor_gif_url;
        }
    }
    
    // Stocker le contexte d'édition
    currentEditingWidgetConfig = { slideId, position, widgetCode };
    
    // Construire le formulaire (async pour source_select)
    const formEl = document.getElementById('widgetConfigForm');
    buildWidgetConfigFormAsync(configDef, currentConfig).then(html => {
        formEl.innerHTML = html;
    });
    
    // Mettre à jour le titre du modal
    const modalTitle = document.querySelector('#widgetConfigModal .modal-title');
    modalTitle.textContent = 'Configurer : ' + configDef.titre;
    
    // Ouvrir le modal
    const modal = new bootstrap.Modal(document.getElementById('widgetConfigModal'));
    modal.show();
}

async function buildWidgetConfigFormAsync(configDef, currentConfig) {
    // Pre-fetch sources for source_select fields
    const sourceTypes = new Set();
    configDef.fields.forEach(f => {
        if (f.type === 'source_select' && f.source_type) {
            sourceTypes.add(f.source_type);
        }
    });
    
    const sourcesCache = {};
    for (const st of sourceTypes) {
        try {
            const resp = await apiCall('/api/sources/by-type/' + st);
            sourcesCache[st] = resp.data || [];
        } catch (e) {
            sourcesCache[st] = [];
        }
    }

    // Précharger les médias déjà uploadés (images/vidéos) pour permettre la re-sélection.
    const mediaCache = { image: [], video: [] };
    try {
        const mediaResp = await apiCall('/api/medias');
        const medias = Array.isArray(mediaResp.data) ? mediaResp.data : [];
        mediaCache.image = medias.filter(m => m && m.type === 'image');
        mediaCache.video = medias.filter(m => m && m.type === 'video');
    } catch (e) {
        mediaCache.image = [];
        mediaCache.video = [];
    }
    
    return configDef.fields.map(field => {
        const value = currentConfig[field.key] !== undefined ? currentConfig[field.key] : field.default;
        return buildFieldHtml(field, value, sourcesCache, mediaCache);
    }).join('');
}

function buildFieldHtml(field, value, sourcesCache, mediaCache) {
    switch (field.type) {
        case 'source_select': {
            const sources = (sourcesCache || {})[field.source_type] || [];
            let optionsHtml = '<option value="">Auto (par défaut)</option>';
            sources.forEach(function(src) {
                const sel = String(value) === String(src.id) ? ' selected' : '';
                const status = src.derniere_erreur ? ' ⚠️' : (src.derniere_sync ? ' ✓' : '');
                const activeLabel = src.actif ? '' : ' (inactive)';
                optionsHtml += '<option value="' + src.id + '"' + sel + '>'
                    + escapeHtml(src.nom) + activeLabel + status + '</option>';
            });
            return '<div class="mb-3">' +
                '<label class="form-label">' + escapeHtml(field.label) + '</label>' +
                '<select class="form-select" data-config-key="' + field.key + '">' +
                optionsHtml +
                '</select>' +
                '<small class="form-text text-muted">Sélectionnez une source configurée dans Paramètres.</small>' +
                '</div>';
        }

        case 'text':
            return '<div class="mb-3">' +
                '<label class="form-label">' + escapeHtml(field.label) + '</label>' +
                '<input type="text" class="form-control" data-config-key="' + field.key + '" ' +
                'value="' + escapeHtml(String(value || '')) + '" ' +
                'placeholder="' + escapeHtml(field.placeholder || '') + '">' +
                '</div>';

        case 'number':
            return '<div class="mb-3">' +
                '<label class="form-label">' + escapeHtml(field.label) + '</label>' +
                '<input type="number" class="form-control" data-config-key="' + field.key + '" ' +
                'value="' + value + '" ' +
                (field.min !== undefined ? 'min="' + field.min + '" ' : '') +
                (field.max !== undefined ? 'max="' + field.max + '" ' : '') + '>' +
                '</div>';

        case 'textarea':
            return '<div class="mb-3">' +
                '<label class="form-label">' + escapeHtml(field.label) + '</label>' +
                '<textarea class="form-control" data-config-key="' + field.key + '" rows="4" ' +
                'placeholder="' + escapeHtml(field.placeholder || '') + '">' +
                escapeHtml(String(value || '')) + '</textarea>' +
                '</div>';

        case 'select':
            return '<div class="mb-3">' +
                '<label class="form-label">' + escapeHtml(field.label) + '</label>' +
                '<select class="form-select" data-config-key="' + field.key + '">' +
                field.options.map(function(opt) {
                    return '<option value="' + escapeHtml(opt.value) + '"' +
                        (String(value) === String(opt.value) ? ' selected' : '') + '>' +
                        escapeHtml(opt.label) + '</option>';
                }).join('') +
                '</select>' +
                '</div>';

        case 'checkbox':
            return '<div class="mb-3">' +
                '<div class="form-check form-switch">' +
                '<input class="form-check-input" type="checkbox" data-config-key="' + field.key + '" ' +
                (value ? 'checked' : '') + '>' +
                '<label class="form-check-label">' + escapeHtml(field.label) + '</label>' +
                '</div>' +
                '</div>';

        case 'image_upload':
            const images = (mediaCache && Array.isArray(mediaCache.image)) ? mediaCache.image : [];
            const imageOptions = ['<option value="">Sélectionner une image déjà uploadée</option>'];
            images.forEach(function(media) {
                const mediaUrl = String(media.url || '');
                const selected = String(value || '') === mediaUrl ? ' selected' : '';
                const label = media.filename || mediaUrl;
                imageOptions.push('<option value="' + escapeHtml(mediaUrl) + '"' + selected + '>' + escapeHtml(label) + '</option>');
            });
            return '<div class="mb-3">' +
                '<label class="form-label">' + escapeHtml(field.label) + '</label>' +
                (value ? '<div class="mb-2"><img src="' + escapeHtml(value) + '" style="max-width:100%;max-height:120px;border-radius:6px;border:1px solid #dee2e6;"></div>' : '') +
                '<input type="hidden" data-config-key="' + field.key + '" value="' + escapeHtml(String(value || '')) + '">' +
                '<select class="form-select mb-2" onchange="selectExistingMedia(this, \'' + field.key + '\', \'image\')">' +
                imageOptions.join('') +
                '</select>' +
                '<input type="file" class="form-control" accept="image/*" onchange="uploadImageField(this, \'' + field.key + '\')">' +
                '<small class="form-text text-muted">JPG, PNG, GIF, WebP, SVG acceptés.</small>' +
                '</div>';

        case 'video_upload':
            const videos = (mediaCache && Array.isArray(mediaCache.video)) ? mediaCache.video : [];
            const videoOptions = ['<option value="">Sélectionner une vidéo déjà uploadée</option>'];
            videos.forEach(function(media) {
                const mediaUrl = String(media.url || '');
                const selected = String(value || '') === mediaUrl ? ' selected' : '';
                const label = media.filename || mediaUrl;
                videoOptions.push('<option value="' + escapeHtml(mediaUrl) + '"' + selected + '>' + escapeHtml(label) + '</option>');
            });
            return '<div class="mb-3">' +
                '<label class="form-label">' + escapeHtml(field.label) + '</label>' +
                '<input type="hidden" data-config-key="' + field.key + '" value="' + escapeHtml(String(value || '')) + '">' +
                (value ? '<div class="mb-2"><small class="text-success"><i class="bi bi-check-circle"></i> ' + escapeHtml(value) + '</small></div>' : '') +
                '<select class="form-select mb-2" onchange="selectExistingMedia(this, \'' + field.key + '\', \'video\')">' +
                videoOptions.join('') +
                '</select>' +
                '<input type="file" class="form-control mb-2" accept="video/mp4,video/webm,video/ogg" onchange="uploadVideoField(this, \'' + field.key + '\')">' +
                '<input type="text" class="form-control" placeholder="Ou saisissez l\'ID YouTube/Dailymotion ou une URL" ' +
                'value="' + escapeHtml(String(value || '')) + '" ' +
                'onchange="this.closest(\'.mb-3\').querySelector(\'[data-config-key=\\x22' + field.key + '\\x22]\').value = this.value">' +
                '<small class="form-text text-muted">Upload MP4/WebM ou saisissez un ID (ex: dQw4w9WgXcQ pour YouTube).</small>' +
                '</div>';

        case 'date':
            return '<div class="mb-3">' +
                '<label class="form-label">' + escapeHtml(field.label) + '</label>' +
                '<input type="date" class="form-control" data-config-key="' + field.key + '" ' +
                'value="' + escapeHtml(String(value || '')) + '">' +
                '</div>';

        case 'time':
            return '<div class="mb-3">' +
                '<label class="form-label">' + escapeHtml(field.label) + '</label>' +
                '<input type="time" class="form-control" data-config-key="' + field.key + '" ' +
                'value="' + escapeHtml(String(value || '')) + '">' +
                '</div>';

        case 'hidden':
            return '<input type="hidden" data-config-key="' + field.key + '" value="' + escapeHtml(String(value || '')) + '">';

        default:
            return '';
    }
}

function buildWidgetConfigForm(configDef, currentConfig) {
    return configDef.fields.map(field => {
        const value = currentConfig[field.key] !== undefined ? currentConfig[field.key] : field.default;
        return buildFieldHtml(field, value, {}, { image: [], video: [] });
    }).join('');
}

async function saveWidgetAdvancedConfig() {
    if (!currentEditingWidgetConfig) return;
    
    const { slideId, position, widgetCode } = currentEditingWidgetConfig;
    const configDef = WIDGET_CONFIG_DEFINITIONS[widgetCode];
    if (!configDef) return;
    
    // Collecter les valeurs du formulaire
    const newConfig = {};
    const formEl = document.getElementById('widgetConfigForm');
    
    configDef.fields.forEach(function(field) {
        const input = formEl.querySelector('[data-config-key="' + field.key + '"]');
        if (!input) return;
        
        switch (field.type) {
            case 'checkbox':
                newConfig[field.key] = input.checked;
                break;
            case 'number':
                newConfig[field.key] = parseInt(input.value) || field.default;
                break;
            case 'source_select':
                newConfig[field.key] = input.value ? parseInt(input.value) : null;
                break;
            default:
                newConfig[field.key] = input.value;
                break;
        }
    });
    
    try {
        // Normaliser/résoudre les URLs GIF avant sauvegarde (ex: lien Tenor non direct)
        if (widgetCode === 'gif' && newConfig.gif_type === 'url' && newConfig.gif_direct_url) {
            const rawGifUrl = String(newConfig.gif_direct_url || '').trim();
            if (rawGifUrl) {
                newConfig.gif_direct_url = rawGifUrl;
                try {
                    const resolved = await apiCall('/api/gif/resolve?url=' + encodeURIComponent(rawGifUrl));
                    if (resolved && resolved.success && resolved.url) {
                        if (resolved.url !== rawGifUrl) {
                            showToast('URL GIF convertie vers un lien direct', 'info');
                        }
                        newConfig.gif_direct_url = resolved.url;
                    }
                } catch (resolveError) {
                    // On garde l'URL brute pour ne pas bloquer l'utilisateur.
                    console.warn('Impossible de résoudre l\'URL GIF:', resolveError);
                    showToast('URL GIF non vérifiée. Utilisez de préférence un lien direct .gif', 'warning');
                }
            }
        }

        // Mettre à jour la config du widget dans la slide
        const slide = currentSlides.find(s => s.id === slideId);
        const widget = slide.widgets.find(w => w.position === position);
        widget.config_json = JSON.stringify(newConfig);
        
        // Sauvegarder via l'API
        await apiCall('/api/slides/' + slideId, 'PUT', {
            nom: slide.nom,
            layout_id: slide.layout_id,
            temps_affichage: slide.temps_affichage,
            actif: slide.actif,
            widgets: slide.widgets.map(function(w) {
                return {
                    widget_id: w.widget_id,
                    position: w.position,
                    config: JSON.parse(w.config_json || '{}')
                };
            })
        });
        
        // Rafraîchir les données
        const updatedSlide = await apiCall('/api/slides/' + slideId);
        const index = currentSlides.findIndex(s => s.id === slideId);
        currentSlides[index] = updatedSlide.data;
        
        renderPreview();
        renderConfig();
        
        bootstrap.Modal.getInstance(document.getElementById('widgetConfigModal')).hide();
        showToast('Configuration sauvegardée', 'success');
        
    } catch (error) {
        showToast('Erreur lors de la sauvegarde', 'error');
        console.error(error);
    }
    
    currentEditingWidgetConfig = null;
}

async function removeWidget(slideId, position) {
    if (!confirm('Retirer ce widget ?')) return;
    
    try {
        const slide = currentSlides.find(s => s.id === slideId);
        slide.widgets = slide.widgets.filter(w => w.position !== position);
        
        // Sauvegarder
        await apiCall(`/api/slides/${slideId}`, 'PUT', {
            nom: slide.nom,
            layout_id: slide.layout_id,
            temps_affichage: slide.temps_affichage,
            actif: slide.actif,
            widgets: slide.widgets.map(w => ({
                widget_id: w.widget_id,
                position: w.position,
                config: JSON.parse(w.config_json || '{}')
            }))
        });
        
        // Rafraîchir
        const updatedSlide = await apiCall(`/api/slides/${slideId}`);
        const index = currentSlides.findIndex(s => s.id === slideId);
        currentSlides[index] = updatedSlide.data;
        
        renderPreview();
        renderConfig();
        showToast('Widget retiré', 'success');
        
    } catch (error) {
        showToast('Erreur lors de la suppression', 'error');
        console.error(error);
    }
}
