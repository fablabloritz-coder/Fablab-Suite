/**
 * DASHBOARD.JS — Phase 1.5 : Système de slides configurables
 * Affichage TV avec cycle automatique des slides
 */

// ========== STATE ==========
let slides = [];
let currentSlideIndex = 0;
let slideTimer = null;
let widgetRefreshTimer = null;

// ========== INIT ==========
let clockTimer = null;

document.addEventListener('DOMContentLoaded', async () => {
    // Horloge temps réel — démarrer immédiatement, avant tout le reste
    startClockTimer();
    
    // Charger le thème
    await loadThemeSettings();
    
    // Charger les slides
    await loadSlides();
    
    // Démarrer le cycle
    try {
        if (slides.length > 0) {
            startSlideCycle();
        } else {
            showEmptyState();
        }
    } catch (error) {
        console.error('Erreur démarrage cycle slides:', error);
    }
});

function startClockTimer() {
    if (clockTimer) clearInterval(clockTimer);
    clockTimer = setInterval(updateClock, 1000);
}

// ========== THÈME ==========
async function loadThemeSettings() {
    try {
        const response = await apiCall('/api/theme');
        const theme = response.data;
        
        // Appliquer le mode (clair/sombre)
        if (theme.mode === 'light') {
            document.body.classList.add('light-mode');
        }
        
        // Appliquer les couleurs
        document.documentElement.style.setProperty('--primary-color', theme.couleur_primaire);
        document.documentElement.style.setProperty('--secondary-color', theme.couleur_secondaire);
        document.documentElement.style.setProperty('--success-color', theme.couleur_succes);
        document.documentElement.style.setProperty('--danger-color', theme.couleur_danger);
        document.documentElement.style.setProperty('--warning-color', theme.couleur_warning);
        document.documentElement.style.setProperty('--info-color', theme.couleur_info);
        
    } catch (error) {
        console.error('Erreur chargement thème:', error);
    }
}

// ========== SLIDES ==========
async function loadSlides() {
    try {
        const response = await apiCall('/api/slides');
        slides = response.data.filter(s => s.actif === 1); // Que les actives
        
        if (slides.length === 0) {
            console.warn('Aucune slide active');
        }
    } catch (error) {
        console.error('Erreur chargement slides:', error);
        showToast('Erreur de chargement des slides', 'error');
    }
}

function showEmptyState() {
    const container = document.getElementById('dashboard-container');
    container.innerHTML = `
        <div class="empty-state">
            <i class="bi bi-tv"></i>
            <h2>Aucune slide configurée</h2>
            <p>Allez dans <a href="/slides">Configuration → Slides</a> pour créer votre première slide</p>
        </div>
    `;
}

// ========== CYCLE DES SLIDES ==========
async function startSlideCycle() {
    await displayCurrentSlide();
    
    // Si une seule slide, pas besoin de cycle
    if (slides.length === 1) {
        startWidgetRefresh();
        return;
    }
    
    // Cycle automatique
    const currentSlide = slides[currentSlideIndex];
    slideTimer = setTimeout(() => {
        nextSlide();
    }, currentSlide.temps_affichage * 1000);
    
    startWidgetRefresh();
}

async function nextSlide() {
    // Arrêter les timers
    if (slideTimer) clearTimeout(slideTimer);
    if (widgetRefreshTimer) clearInterval(widgetRefreshTimer);
    
    // Passer à la slide suivante
    currentSlideIndex = (currentSlideIndex + 1) % slides.length;
    
    // Transition
    const container = document.getElementById('dashboard-container');
    container.classList.add('slide-transition');
    
    setTimeout(async () => {
        await displayCurrentSlide();
        container.classList.remove('slide-transition');
        
        // Relancer le cycle
        const currentSlide = slides[currentSlideIndex];
        slideTimer = setTimeout(() => {
            nextSlide();
        }, currentSlide.temps_affichage * 1000);
        
        startWidgetRefresh();
    }, 500); // Durée de la transition
}

function startWidgetRefresh() {
    // Rafraîchir les widgets toutes les 10 secondes
    if (widgetRefreshTimer) clearInterval(widgetRefreshTimer);
    
    widgetRefreshTimer = setInterval(() => {
        refreshCurrentSlideWidgets();
    }, 10000);
}

// ========== AFFICHAGE DE LA SLIDE ==========
async function displayCurrentSlide() {
    const slide = slides[currentSlideIndex];
    const container = document.getElementById('dashboard-container');
    
    // Parser la grille
    const grille = JSON.parse(slide.grille_json);
    
    // Construire le style de la grille
    const gridStyle = `
        grid-template-columns: repeat(${slide.colonnes}, 1fr);
        grid-template-rows: repeat(${slide.lignes}, 1fr);
    `;
    
    // Afficher loading pendant le rendu des widgets
    container.innerHTML = `
        <div class="dashboard-grid" style="${gridStyle}">
            ${grille.map((pos, index) => {
                const style = `
                    grid-column: ${pos.x + 1} / span ${pos.w};
                    grid-row: ${pos.y + 1} / span ${pos.h};
                `;
                return `
                    <div class="dashboard-widget" style="${style}" data-position="${index}">
                        <div class="spinner-border text-primary" role="status"></div>
                    </div>
                `;
            }).join('')}
        </div>
        
        <!-- Indicateur slide -->
        <div class="slide-indicator">
            ${slides.map((s, i) => `
                <span class="slide-dot ${i === currentSlideIndex ? 'active' : ''}"></span>
            `).join('')}
        </div>
    `;
    
    // Rendre chaque widget de manière asynchrone
    for (let index = 0; index < grille.length; index++) {
        const widgetData = slide.widgets.find(w => w.position === index);
        const widgetElement = container.querySelector(`[data-position="${index}"]`);
        
        if (widgetData) {
            const widgetHTML = await renderWidget(widgetData);
            widgetElement.innerHTML = widgetHTML;
        } else {
            widgetElement.innerHTML = renderEmptyWidget(index);
        }
    }
    
    // Mettre à jour l'horloge immédiatement après le rendu
    updateClock();
}

function renderEmptyWidget(position) {
    return `
        <div class="widget-empty">
            <i class="bi bi-inbox"></i>
            <p>Position ${position + 1}</p>
        </div>
    `;
}

// ========== RENDU DES WIDGETS ==========
async function renderWidget(widgetData) {
    const widgetCode = widgetData.widget_code;
    const config = JSON.parse(widgetData.config_json || '{}');
    const sourceId = config.source_id || null;
    
    try {
        // Charger le template du widget depuis le serveur
        const response = await fetch(`/api/widgets/${widgetCode}/render`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config, source_id: sourceId })
        });
        
        if (!response.ok) {
            throw new Error(`Erreur ${response.status}`);
        }
        
        const result = await response.json();
        
        if (result.success) {
            return result.html;
        } else {
            return renderWidgetError(widgetData, result.error);
        }
    } catch (error) {
        console.error(`Erreur rendu widget ${widgetCode}:`, error);
        return renderWidgetError(widgetData, error.message);
    }
}

function renderWidgetError(widgetData, errorMsg) {
    return `
        <div class="widget-error">
            <i class="bi bi-exclamation-triangle"></i>
            <p>${widgetData.widget_nom}</p>
            <small>${errorMsg}</small>
        </div>
    `;
}

function renderWidgetCompteurs(widgetData) {
    const config = JSON.parse((widgetData && widgetData.config_json) || '{}');
    return `
        <div class="widget-compteurs" data-config='${JSON.stringify(config).replace(/'/g, "&#39;")}'>
            <h3><i class="bi bi-bar-chart"></i> Activités</h3>
            <div class="compteurs-grid" id="widget-compteurs-data">
                <div class="spinner-border" role="status"></div>
            </div>
        </div>
    `;
}

function renderWidgetActivites(widgetData) {
    const config = JSON.parse((widgetData && widgetData.config_json) || '{}');
    return `
        <div class="widget-activites" data-config='${JSON.stringify(config).replace(/'/g, "&#39;")}'>
            <h3><i class="bi bi-list-check"></i> Activités en cours</h3>
            <div class="activites-list" id="widget-activites-data">
                <div class="spinner-border" role="status"></div>
            </div>
        </div>
    `;
}

function renderWidgetHorloge(widgetData) {
    const config = JSON.parse((widgetData && widgetData.config_json) || '{}');
    const now = new Date();
    
    // Options configurables
    const format24h = config.format !== '12h';
    const afficherSecondes = config.afficher_secondes !== false;
    const afficherDate = config.afficher_date !== false;
    
    // Formatage de l'heure
    let timeOptions = { hour: '2-digit', minute: '2-digit', hour12: !format24h };
    if (afficherSecondes) timeOptions.second = '2-digit';
    const timeStr = now.toLocaleTimeString('fr-FR', timeOptions);
    
    // Formatage de la date
    const dateStr = now.toLocaleDateString('fr-FR', {
        weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'
    });
    
    return `
        <div class="widget-horloge" data-config='${JSON.stringify(config).replace(/'/g, "&#39;")}'>
            <div class="horloge-time" id="widget-horloge-time">${timeStr}</div>
            ${afficherDate ? '<div class="horloge-date" id="widget-horloge-date">' + dateStr + '</div>' : ''}
        </div>
    `;
}

function renderWidgetCalendrier(widgetData) {
    return `
        <div class="widget-calendrier">
            <h3><i class="bi bi-calendar"></i> Événements à venir</h3>
            <div class="calendrier-list" id="widget-calendrier-data">
                <div class="spinner-border" role="status"></div>
            </div>
        </div>
    `;
}

function renderWidgetFabtrackStats(widgetData) {
    return `
        <div class="widget-fabtrack-stats">
            <h3><i class="bi bi-graph-up"></i> Fabtrack</h3>
            <div class="fabtrack-stats-grid" id="widget-fabtrack-stats-data">
                <div class="spinner-border" role="status"></div>
            </div>
        </div>
    `;
}

function renderWidgetFabtrackMachines(widgetData) {
    return `
        <div class="widget-fabtrack-machines">
            <h3><i class="bi bi-tools"></i> Machines</h3>
            <div class="machines-grid" id="widget-fabtrack-machines-data">
                <div class="spinner-border" role="status"></div>
            </div>
        </div>
    `;
}

function renderWidgetFabtrackConso(widgetData) {
    return `
        <div class="widget-fabtrack-conso">
            <h3><i class="bi bi-receipt"></i> Dernières consommations</h3>
            <div class="conso-list" id="widget-fabtrack-conso-data">
                <div class="spinner-border" role="status"></div>
            </div>
        </div>
    `;
}

function renderWidgetImprimantes(widgetData) {
    return `
        <div class="widget-imprimantes">
            <h3><i class="bi bi-printer"></i> Imprimantes 3D</h3>
            <div class="imprimantes-grid" id="widget-imprimantes-data">
                <div class="spinner-border" role="status"></div>
            </div>
        </div>
    `;
}

function renderWidgetMeteo(widgetData) {
    return `
        <div class="widget-meteo">
            <h3><i class="bi bi-cloud-sun"></i> Météo</h3>
            <div id="widget-meteo-data">
                <p class="text-muted">À venir</p>
            </div>
        </div>
    `;
}

function renderWidgetTexteLibre(widgetData) {
    const config = JSON.parse(widgetData.config_json || '{}');
    const taille = config.taille_texte || 'normal';
    const alignement = config.alignement || 'left';
    const tailleMap = { small: '0.875rem', normal: '1.125rem', large: '1.5rem', xlarge: '2rem' };
    const fontSize = tailleMap[taille] || tailleMap.normal;
    return `
        <div class="widget-texte-libre">
            <h3>${escapeHtml(config.titre || 'Information')}</h3>
            <div class="texte-content" style="font-size:${fontSize}; text-align:${alignement}; line-height:1.6;">
                ${escapeHtml(config.texte || 'Texte personnalisé')}
            </div>
        </div>
    `;
}

// ========== RAFRAÎCHISSEMENT DES DONNÉES ==========
async function refreshCurrentSlideWidgets() {
    const slide = slides[currentSlideIndex];
    
    for (const widgetData of slide.widgets) {
        await refreshWidget(widgetData.widget_code);
    }
}

async function refreshWidget(widgetCode) {
    try {
        switch (widgetCode) {
            case 'compteurs':
                await refreshWidgetCompteurs();
                break;
            case 'activites':
                await refreshWidgetActivites();
                break;
            case 'horloge':
                refreshWidgetHorloge();
                break;
            case 'calendrier':
                await refreshWidgetCalendrier();
                break;
            case 'fabtrack_stats':
                await refreshWidgetFabtrackStats();
                break;
            case 'imprimantes':
                await refreshWidgetImprimantes();
                break;
        }
    } catch (error) {
        console.error(`Erreur rafraîchissement widget ${widgetCode}:`, error);
    }
}

async function refreshWidgetCompteurs() {
    const el = document.getElementById('widget-compteurs-data');
    if (!el) return;
    
    try {
        const data = await apiCall('/api/dashboard/data');
        const compteurs = data.compteurs || {};
        
        el.innerHTML = `
            <div class="compteur-item">
                <div class="compteur-value">${compteurs.interventions_total || 0}</div>
                <div class="compteur-label">Interventions</div>
            </div>
            <div class="compteur-item">
                <div class="compteur-value">${compteurs.impression_3d_grammes || 0}</div>
                <div class="compteur-label">3D (g)</div>
            </div>
            <div class="compteur-item">
                <div class="compteur-value">${compteurs.decoupe_m2 || 0}</div>
                <div class="compteur-label">Découpe (m²)</div>
            </div>
            <div class="compteur-item">
                <div class="compteur-value">${compteurs.papier_feuilles || 0}</div>
                <div class="compteur-label">Papier (feuilles)</div>
            </div>
        `;
    } catch (error) {
        el.innerHTML = '<p class="text-muted">Erreur chargement</p>';
    }
}

async function refreshWidgetActivites() {
    const el = document.getElementById('widget-activites-data');
    if (!el) return;
    
    try {
        const data = await apiCall('/api/dashboard/data');
        const activites = data.activites || [];
        
        if (activites.length === 0) {
            el.innerHTML = '<p class="text-muted">Aucune consommation récente</p>';
            return;
        }
        
        el.innerHTML = activites.map(a => `
            <div class="activite-item">
                <div class="activite-titre">${escapeHtml(a.type_activite_nom || a.nom_type_activite || 'Activité')}</div>
                <div class="activite-meta">
                    <span class="badge bg-secondary">${escapeHtml(a.machine_nom || a.nom_machine || 'Machine n/a')}</span>
                    <span>${escapeHtml(a.preparateur_nom || a.nom_preparateur || 'Préparateur n/a')}</span>
                </div>
            </div>
        `).join('');
    } catch (error) {
        el.innerHTML = '<p class="text-muted">Erreur chargement</p>';
    }
}

function refreshWidgetHorloge() {
    const elTime = document.getElementById('widget-horloge-time');
    const elDate = document.getElementById('widget-horloge-date');
    
    if (!elTime) return;
    
    // Lire la config depuis le DOM
    const horlogeEl = document.querySelector('.widget-horloge');
    let config = {};
    if (horlogeEl && horlogeEl.dataset.config) {
        try { config = JSON.parse(horlogeEl.dataset.config); } catch(e) {}
    }
    
    const now = new Date();
    const format24h = config.format !== '12h';
    const afficherSecondes = config.afficher_secondes !== false;
    
    let timeOptions = { hour: '2-digit', minute: '2-digit', hour12: !format24h };
    if (afficherSecondes) timeOptions.second = '2-digit';
    
    elTime.textContent = now.toLocaleTimeString('fr-FR', timeOptions);
    
    if (elDate) {
        elDate.textContent = now.toLocaleDateString('fr-FR', {
            weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'
        });
    }
}

async function refreshWidgetCalendrier() {
    const el = document.getElementById('widget-calendrier-data');
    if (!el) return;
    
    el.innerHTML = '<p class="text-muted">CalDAV non encore intégré (Phase 3)</p>';
}

async function refreshWidgetFabtrackStats() {
    const el = document.getElementById('widget-fabtrack-stats-data');
    if (!el) return;

    try {
        const data = await apiCall('/api/dashboard/data');
        const stats = data.fabtrack_stats || {};

        el.innerHTML = `
            <div class="compteur-item">
                <div class="compteur-value">${stats.total_interventions || 0}</div>
                <div class="compteur-label">Total interventions</div>
            </div>
            <div class="compteur-item">
                <div class="compteur-value">${stats.total_papier_feuilles || 0}</div>
                <div class="compteur-label">Feuilles papier</div>
            </div>
            <div class="compteur-item">
                <div class="compteur-value">${stats.total_papier_couleur || 0}</div>
                <div class="compteur-label">Papier couleur</div>
            </div>
            <div class="compteur-item">
                <div class="compteur-value">${stats.total_papier_nb || 0}</div>
                <div class="compteur-label">Papier N&B</div>
            </div>
        `;
    } catch (error) {
        el.innerHTML = '<p class="text-muted">Erreur Fabtrack</p>';
    }
}

async function refreshWidgetImprimantes() {
    const el = document.getElementById('widget-imprimantes-data');
    if (!el) return;
    
    el.innerHTML = '<p class="text-muted">Imprimantes non encore intégrées (Phase 4)</p>';
}

// ========== HORLOGE GLOBALE ==========
function updateClock() {
    try {
        refreshWidgetHorloge();
    } catch (e) {
        // Silencieux — ne pas bloquer le timer
    }
}
