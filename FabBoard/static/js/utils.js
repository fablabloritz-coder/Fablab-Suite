// FabBoard — Utilitaires JavaScript

/**
 * Affiche une notification toast
 * @param {string} message - Le message à afficher
 * @param {string} type - Type : success, error, warning, info
 */
function showToast(message, type = 'info') {
    const toastContainer = document.getElementById('toast-container');
    if (!toastContainer) return;
    
    const colors = {
        success: 'bg-success',
        error: 'bg-danger',
        warning: 'bg-warning',
        info: 'bg-info'
    };
    
    const toastEl = document.createElement('div');
    toastEl.className = `toast align-items-center text-white ${colors[type] || colors.info} border-0`;
    toastEl.setAttribute('role', 'alert');
    toastEl.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">${message}</div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
        </div>
    `;
    
    toastContainer.appendChild(toastEl);
    const toast = new bootstrap.Toast(toastEl, { delay: 3000 });
    toast.show();
    
    toastEl.addEventListener('hidden.bs.toast', () => {
        toastEl.remove();
    });
}

/**
 * Formate une date ISO en format français
 * @param {string} isoString - Date au format ISO
 * @returns {string} Date formatée
 */
function formatDate(isoString) {
    if (!isoString) return '--';
    const date = new Date(isoString);
    return date.toLocaleDateString('fr-FR', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

/**
 * Formate une date ISO en format court (DD/MM à HH:MM)
 * @param {string} isoString - Date au format ISO
 * @returns {string} Date formatée
 */
function formatDateShort(isoString) {
    if (!isoString) return '--';
    const date = new Date(isoString);
    return date.toLocaleDateString('fr-FR', {
        day: '2-digit',
        month: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
}

/**
 * Convertit une date locale en format ISO pour SQLite
 * @param {string} localDatetime - Date du datetime-local input
 * @returns {string} Date ISO
 */
function toISOString(localDatetime) {
    if (!localDatetime) return '';
    return localDatetime.replace('T', ' ');
}

/**
 * Convertit une date ISO en format datetime-local
 * @param {string} isoString - Date ISO
 * @returns {string} Format datetime-local
 */
function toDatetimeLocal(isoString) {
    if (!isoString) return '';
    return isoString.replace(' ', 'T').substring(0, 16);
}

/**
 * Récupère la classe CSS pour un badge d'urgence
 * @param {string} niveau - Niveau d'urgence
 * @returns {string} Classe CSS
 */
function getBadgeUrgenceClass(niveau) {
    const classes = {
        'critique': 'badge-critique',
        'urgent': 'badge-urgent',
        'normal': 'badge-normal',
        'faible': 'badge-faible'
    };
    return classes[niveau] || 'badge-normal';
}

/**
 * Récupère la classe CSS pour un badge de statut
 * @param {string} statut - Statut de l'activité
 * @returns {string} Classe CSS
 */
function getBadgeStatutClass(statut) {
    const classes = {
        'en_attente': 'badge-en_attente',
        'en_cours': 'badge-en_cours',
        'termine': 'badge-termine',
        'annule': 'badge-annule'
    };
    return classes[statut] || 'badge-en_attente';
}

/**
 * Récupère le label français pour un statut
 * @param {string} statut - Statut de l'activité
 * @returns {string} Label
 */
function getStatutLabel(statut) {
    const labels = {
        'en_attente': 'En attente',
        'en_cours': 'En cours',
        'termine': 'Terminé',
        'annule': 'Annulé'
    };
    return labels[statut] || statut;
}

/**
 * Récupère le label français pour un niveau d'urgence
 * @param {string} niveau - Niveau d'urgence
 * @returns {string} Label
 */
function getUrgenceLabel(niveau) {
    const labels = {
        'auto': 'Auto',
        'critique': 'Critique',
        'urgent': 'Urgent',
        'normal': 'Normal',
        'faible': 'Faible'
    };
    return labels[niveau] || niveau;
}

/**
 * Échappe le HTML pour éviter les injections XSS
 * @param {string} text - Texte à échapper
 * @returns {string} Texte échappé
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Requête API générique
 * @param {string} url - URL de l'API
 * @param {string|object} methodOrOptions - Méthode HTTP (GET, POST, PUT, DELETE, PATCH) ou options directes
 * @param {object} data - Données à envoyer (optionnel)
 * @returns {Promise} Réponse JSON
 */
async function apiCall(url, methodOrOptions = {}, data = null) {
    try {
        let options = {};
        
        // Si le 2ème paramètre est une string, c'est la méthode HTTP
        if (typeof methodOrOptions === 'string') {
            options = {
                method: methodOrOptions.toUpperCase(),
                headers: {
                    'Content-Type': 'application/json'
                }
            };
            if (data) {
                options.body = JSON.stringify(data);
            }
        } else {
            // Sinon c'est l'objet options complet
            options = {
                headers: {
                    'Content-Type': 'application/json',
                    ...methodOrOptions.headers
                },
                ...methodOrOptions
            };
        }
        
        const response = await fetch(url, options);
        
        // Lire la réponse en texte d'abord pour éviter le crash JSON.parse sur du HTML
        const text = await response.text();
        let result;
        try {
            result = JSON.parse(text);
        } catch (e) {
            throw new Error(`Erreur serveur (HTTP ${response.status})`);
        }
        
        if (!response.ok) {
            throw new Error(result.error || `Erreur HTTP ${response.status}`);
        }
        
        return result;
    } catch (error) {
        console.error('API Error:', error);
        throw error;
    }
}
