// ============================================================
//  PRETGO — JavaScript
// ============================================================

// ============================================================
//  UTILITAIRE : Échappement HTML (anti-XSS)
// ============================================================
function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// ============================================================
//  AUTOCOMPLÉTION POUR LA RECHERCHE DE PERSONNES
// ============================================================

function initAutocomplete() {
    const searchInput = document.getElementById('personne-search');
    const searchBox = searchInput ? searchInput.closest('.search-box') : null;
    const resultsDiv = document.getElementById('autocomplete-results');
    const personneIdInput = document.getElementById('personne_id');
    const selectedDiv = document.getElementById('personne-selected');

    if (!searchInput) return;

    let timeout = null;

    searchInput.addEventListener('input', function () {
        const rawQuery = this.value || '';
        const query = rawQuery.replace(/\s+/g, ' ').trim();
        clearTimeout(timeout);

        if (query.length < 1) {
            resultsDiv.style.display = 'none';
            return;
        }

        timeout = setTimeout(() => {
            fetch('/api/personnes?q=' + encodeURIComponent(query))
                .then(response => response.json())
                .then(data => {
                    resultsDiv.innerHTML = '';

                    if (data.length === 0) {
                        resultsDiv.innerHTML =
                            '<div class="autocomplete-item"><em>Aucun résultat — '
                            + '<a href="/personnes/ajouter">Ajouter une personne</a></em></div>';
                        resultsDiv.style.display = 'block';
                        return;
                    }

                    data.forEach(personne => {
                        const item = document.createElement('div');
                        item.className = 'autocomplete-item';

                        const catLabel = personne.categorie_label || personne.categorie || '';

                        item.innerHTML =
                            '<div class="name">' + escapeHtml(personne.nom) + ' ' + escapeHtml(personne.prenom) + '</div>'
                            + '<div class="details">'
                            + '<span>' + escapeHtml(catLabel) + '</span>'
                            + (personne.classe ? ' — ' + escapeHtml(personne.classe) : '')
                            + (personne.email ? ' — <i class="bi bi-envelope"></i> ' + escapeHtml(personne.email) : '')
                            + '</div>';

                        item.addEventListener('click', () => {
                            personneIdInput.value = personne.id;
                            searchInput.value = personne.nom + ' ' + personne.prenom;
                            if (searchBox) {
                                searchBox.style.display = 'none';
                            } else {
                                searchInput.style.display = 'none';
                            }

                            selectedDiv.innerHTML =
                                '<div class="alert alert-info d-flex justify-content-between align-items-center mb-0">'
                                + '<div>'
                                + '<strong>' + escapeHtml(personne.nom) + ' ' + escapeHtml(personne.prenom) + '</strong> '
                                + '<span class="ms-2">' + escapeHtml(catLabel) + '</span>'
                                + (personne.classe ? '<span class="ms-2">' + escapeHtml(personne.classe) + '</span>' : '')
                                + '</div>'
                                + '<button type="button" class="btn btn-sm btn-outline-secondary" onclick="resetPersonne()">'
                                + '<i class="bi bi-x-lg"></i> Changer'
                                + '</button>'
                                + '</div>';
                            selectedDiv.style.display = 'block';
                            resultsDiv.style.display = 'none';
                        });

                        resultsDiv.appendChild(item);
                    });

                    resultsDiv.style.display = 'block';
                })
                .catch(err => {
                    console.error('Erreur autocomplétion:', err);
                });
        }, 200);
    });

    // Fermer l'autocomplétion en cliquant ailleurs
    document.addEventListener('click', function (e) {
        if (!searchInput.contains(e.target) && !resultsDiv.contains(e.target)) {
            resultsDiv.style.display = 'none';
        }
    });
}

function resetPersonne() {
    const searchInput = document.getElementById('personne-search');
    const searchBox = searchInput ? searchInput.closest('.search-box') : null;
    const personneIdInput = document.getElementById('personne_id');
    const selectedDiv = document.getElementById('personne-selected');

    personneIdInput.value = '';
    searchInput.value = '';
    if (searchBox) {
        searchBox.style.display = 'block';
    } else {
        searchInput.style.display = 'block';
    }
    selectedDiv.style.display = 'none';
    searchInput.focus();
}


// ============================================================
//  HORLOGE EN TEMPS RÉEL
// ============================================================

function updateClock() {
    const clockEl = document.getElementById('current-datetime');
    if (!clockEl) return;

    const now = new Date();
    const options = {
        weekday: 'long',
        year: 'numeric',
        month: 'long',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    };
    clockEl.textContent = now.toLocaleDateString('fr-FR', options);
}


// ============================================================
//  INITIALISATION AU CHARGEMENT DE LA PAGE
// ============================================================

document.addEventListener('DOMContentLoaded', function () {
    // Autocomplétion
    initAutocomplete();

    // Horloge
    updateClock();
    setInterval(updateClock, 1000);

    // Mettre en surbrillance le lien de navigation actif
    const currentPath = window.location.pathname;
    document.querySelectorAll('.btn-nav').forEach(link => {
        const href = link.getAttribute('href');
        if (href === currentPath || (currentPath.startsWith(href) && href !== '/')) {
            link.classList.add('active');
        } else if (href === '/' && currentPath === '/') {
            link.classList.add('active');
        }
    });

    // Auto-fermer les alertes après 5 secondes
    document.querySelectorAll('.alert-dismissible').forEach(alert => {
        setTimeout(() => {
            const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
            if (bsAlert) bsAlert.close();
        }, 5000);
    });

    // ============================================================
    //  TRI DES COLONNES DE TABLEAUX (.table-sortable)
    // ============================================================
    document.querySelectorAll('table.table-sortable').forEach(function(table) {
        var headers = table.querySelectorAll('thead th[data-sort]');
        headers.forEach(function(th, colIndex) {
            th.style.cursor = 'pointer';
            th.style.userSelect = 'none';
            // Ajouter l'icône de tri
            var icon = document.createElement('i');
            icon.className = 'bi bi-arrow-down-up ms-1 text-muted';
            icon.style.fontSize = '0.75em';
            th.appendChild(icon);

            th.addEventListener('click', function() {
                var tbody = table.querySelector('tbody');
                if (!tbody) return;
                var rows = Array.from(tbody.querySelectorAll('tr'));
                var sortType = th.getAttribute('data-sort'); // 'text', 'num', 'date'
                var asc = th.getAttribute('data-sort-dir') !== 'asc';
                th.setAttribute('data-sort-dir', asc ? 'asc' : 'desc');

                // Reset toutes les icônes
                headers.forEach(function(h) {
                    var ic = h.querySelector('i.bi');
                    if (ic) ic.className = 'bi bi-arrow-down-up ms-1 text-muted';
                });
                icon.className = asc ? 'bi bi-arrow-up ms-1' : 'bi bi-arrow-down ms-1';

                var idx = Array.from(th.parentNode.children).indexOf(th);
                rows.sort(function(a, b) {
                    var aText = (a.children[idx] ? a.children[idx].textContent.trim() : '');
                    var bText = (b.children[idx] ? b.children[idx].textContent.trim() : '');
                    if (sortType === 'num') {
                        var aNum = parseFloat(aText.replace(/[^\d.,-]/g, '').replace(',', '.')) || 0;
                        var bNum = parseFloat(bText.replace(/[^\d.,-]/g, '').replace(',', '.')) || 0;
                        return asc ? aNum - bNum : bNum - aNum;
                    } else if (sortType === 'date') {
                        // Format DD/MM/YYYY ou YYYY-MM-DD
                        var aParts = aText.match(/(\d{2})\/(\d{2})\/(\d{4})/);
                        var bParts = bText.match(/(\d{2})\/(\d{2})\/(\d{4})/);
                        var aDate = aParts ? new Date(aParts[3], aParts[2]-1, aParts[1]) : new Date(aText);
                        var bDate = bParts ? new Date(bParts[3], bParts[2]-1, bParts[1]) : new Date(bText);
                        return asc ? aDate - bDate : bDate - aDate;
                    } else {
                        return asc ? aText.localeCompare(bText, 'fr') : bText.localeCompare(aText, 'fr');
                    }
                });
                rows.forEach(function(row) { tbody.appendChild(row); });
            });
        });
    });
});
