// ============================================================
//  PRETGO — Scanner & Douchette (logique extraite de base.html)
// ============================================================
(function() {
    'use strict';

    var body = document.body;
    var modeScanner = body.getAttribute('data-mode-scanner') || '';
    var scanUrl = body.getAttribute('data-scan-url') || '/api/scan';
    var html5QrCode = null;

    // ── Bip sonore ──
    function getBipSettings() {
        var v = 0.15, t = 'sine';
        try {
            var storedV = localStorage.getItem('pretgo_bip_volume');
            if (storedV !== null) v = Math.max(0, Math.min(1, parseFloat(storedV)));
            var storedT = localStorage.getItem('pretgo_bip_type');
            if (storedT) t = storedT;
        } catch(e) {}
        return {volume: v, type: t};
    }

    function playBeep(vol, type) {
        try {
            var settings = getBipSettings();
            var volume = (typeof vol === 'number' ? vol : settings.volume);
            var bipType = type || settings.type;
            var ctx = new (window.AudioContext || window.webkitAudioContext)();
            if (bipType === 'double') {
                var o1 = ctx.createOscillator();
                var g1 = ctx.createGain();
                o1.type = 'sine';
                o1.frequency.value = 1200;
                g1.gain.value = volume;
                o1.connect(g1); g1.connect(ctx.destination);
                o1.start();
                setTimeout(function() { o1.stop(); }, 70);
                setTimeout(function() {
                    var o2 = ctx.createOscillator();
                    var g2 = ctx.createGain();
                    o2.type = 'sine';
                    o2.frequency.value = 1200;
                    g2.gain.value = volume;
                    o2.connect(g2); g2.connect(ctx.destination);
                    o2.start();
                    setTimeout(function() { o2.stop(); ctx.close(); }, 70);
                }, 90);
                return;
            } else if (bipType === 'up' || bipType === 'down') {
                var o = ctx.createOscillator();
                var g = ctx.createGain();
                o.type = 'sine';
                o.frequency.value = (bipType === 'up') ? 900 : 1800;
                g.gain.value = volume;
                o.connect(g); g.connect(ctx.destination);
                o.start();
                var start = ctx.currentTime;
                if (bipType === 'up') {
                    o.frequency.linearRampToValueAtTime(1800, start + 0.13);
                } else {
                    o.frequency.linearRampToValueAtTime(900, start + 0.13);
                }
                setTimeout(function() { o.stop(); ctx.close(); }, 130);
                return;
            } else {
                var osc = ctx.createOscillator();
                var gain = ctx.createGain();
                osc.type = bipType;
                osc.frequency.value = 1200;
                gain.gain.value = volume;
                osc.connect(gain); gain.connect(ctx.destination);
                osc.start();
                setTimeout(function() { osc.stop(); ctx.close(); }, 120);
            }
        } catch(e) {}
    }

    // Rendre playBeep accessible globalement (utilisé par d'autres pages)
    window.playBeep = playBeep;
    window.getBipSettings = getBipSettings;

    function showDouchetteHelp() {
        var toast = document.getElementById('douchette-toast');
        var toastBody = document.getElementById('douchette-toast-body');
        if (!toast || !toastBody) return;
        toastBody.innerHTML = '<i class="bi bi-upc-scan"></i> Scannez le code-barres avec la douchette (clavier)';
        toast.style.display = 'block';
        setTimeout(function() { toast.style.display = 'none'; }, 4000);
    }

    function openWebcamModal() {
        var modal = new bootstrap.Modal(document.getElementById('modalScanGlobal'));
        modal.show();
    }

    // ── Boutons scanner ──
    if (modeScanner === 'webcam') {
        var btnScan = document.getElementById('btn-scan-global');
        if (btnScan) {
            btnScan.addEventListener('click', function(e) {
                e.preventDefault();
                openWebcamModal();
            });
        }
    } else if (modeScanner === 'douchette') {
        var btnScanD = document.getElementById('btn-scan-global');
        if (btnScanD) {
            btnScanD.addEventListener('click', function(e) {
                e.preventDefault();
                showDouchetteHelp();
            });
        }
    } else if (modeScanner === 'les_deux') {
        var btnWebcam = document.getElementById('btn-scan-webcam');
        var btnDouchette = document.getElementById('btn-scan-douchette');
        if (btnWebcam) {
            btnWebcam.addEventListener('click', function(e) {
                e.preventDefault();
                var toast = document.getElementById('douchette-toast');
                if (toast) toast.style.display = 'none';
                openWebcamModal();
            });
        }
        if (btnDouchette) {
            btnDouchette.addEventListener('click', function(e) {
                e.preventDefault();
                var modalEl = document.getElementById('modalScanGlobal');
                var modalInstance = bootstrap.Modal.getInstance(modalEl);
                if (modalInstance) modalInstance.hide();
                if (html5QrCode) try { html5QrCode.stop(); } catch(ex) {}
                showDouchetteHelp();
            });
        }
    }

    // ── Scanner webcam modal ──
    var modalScan = document.getElementById('modalScanGlobal');
    if (modalScan) {
        function handleScanResult(decoded) {
            playBeep();
            html5QrCode.stop().then(function() {
                var hint = document.getElementById('global-scan-hint');
                var result = document.getElementById('global-scan-result');
                hint.textContent = 'Recherche en cours...';
                result.style.display = 'none';
                fetch(scanUrl + '?code=' + encodeURIComponent(decoded))
                    .then(function(r) { return r.json(); })
                    .then(function(data) {
                        if (data.found) {
                            setTimeout(function() { window.location.href = data.url; }, 200);
                        } else {
                            result.innerHTML = '<div class="alert alert-warning mb-0"><i class="bi bi-exclamation-circle"></i> ' + data.message + '</div>';
                            result.style.display = 'block';
                            hint.textContent = '';
                            setTimeout(function() { restartScanner(); }, 2000);
                        }
                    }).catch(function() {
                        result.innerHTML = '<div class="alert alert-danger mb-0">Erreur de connexion.</div>';
                        result.style.display = 'block';
                        hint.textContent = '';
                    });
            });
        }

        function restartScanner() {
            var hint = document.getElementById('global-scan-hint');
            var result = document.getElementById('global-scan-result');
            try { if (html5QrCode) html5QrCode.stop().catch(function() {}); } catch(e) {}
            html5QrCode = new Html5Qrcode("global-scanner-reader");
            hint.textContent = 'Placez le code-barres devant la caméra';
            result.style.display = 'none';
            html5QrCode.start(
                { facingMode: "environment" },
                { fps: 10, qrbox: { width: 250, height: 100 } },
                function(decoded) {
                    html5QrCode.stop().then(function() {
                        var h = document.getElementById('global-scan-hint');
                        var r = document.getElementById('global-scan-result');
                        h.textContent = 'Recherche en cours...';
                        r.style.display = 'none';
                        fetch(scanUrl + '?code=' + encodeURIComponent(decoded))
                            .then(function(res) { return res.json(); })
                            .then(function(data) {
                                if (data.found) {
                                    window.location.href = data.url;
                                } else {
                                    r.innerHTML = '<div class="alert alert-warning mb-0"><i class="bi bi-exclamation-circle"></i> ' + data.message + '</div>';
                                    r.style.display = 'block';
                                    h.textContent = '';
                                    setTimeout(function() { restartScanner(); }, 2000);
                                }
                            }).catch(function() {});
                    });
                }, function() {}
            ).catch(function() {});
        }

        modalScan.addEventListener('shown.bs.modal', function() {
            var reader = document.getElementById('global-scanner-reader');
            var hint = document.getElementById('global-scan-hint');
            var result = document.getElementById('global-scan-result');
            reader.innerHTML = '';
            hint.textContent = 'Placez le code-barres devant la caméra';
            result.style.display = 'none';

            if (html5QrCode) try { html5QrCode.stop(); } catch(e) {}
            html5QrCode = new Html5Qrcode("global-scanner-reader");
            html5QrCode.start(
                { facingMode: "environment" },
                { fps: 10, qrbox: { width: 250, height: 100 } },
                handleScanResult,
                function() {}
            ).catch(function(err) {
                reader.innerHTML =
                    '<div class="alert alert-warning text-center"><i class="bi bi-camera-video-off"></i> Caméra non disponible<br><small>' + err + '</small></div>';
            });
        });

        modalScan.addEventListener('hidden.bs.modal', function() {
            if (html5QrCode) try { html5QrCode.stop(); } catch(e) {}
        });
    }

    // ── Douchette : capture clavier rapide ──
    if (modeScanner !== 'webcam') {
        var buffer = '';
        var lastKeyTime = 0;
        var THRESHOLD = 50;
        var MIN_LEN = 3;

        document.addEventListener('keydown', function(e) {
            var tag = document.activeElement.tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || document.activeElement.isContentEditable) return;
            if (e.ctrlKey || e.altKey || e.metaKey) return;

            var now = Date.now();
            if (now - lastKeyTime > 300) buffer = '';
            lastKeyTime = now;

            if (e.key === 'Enter') {
                if (buffer.length >= MIN_LEN) {
                    e.preventDefault();
                    lookupCode(buffer);
                }
                buffer = '';
                return;
            }
            if (e.key.length === 1) {
                buffer += e.key;
            }
        });

        function lookupCode(code) {
            var toast = document.getElementById('douchette-toast');
            var toastBody = document.getElementById('douchette-toast-body');
            if (!toast || !toastBody) return;
            toastBody.innerHTML = '<i class="bi bi-upc-scan"></i> Recherche de « ' + code + ' »...';
            toast.style.display = 'block';

            fetch(scanUrl + '?code=' + encodeURIComponent(code))
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    if (data.found) {
                        window.location.href = data.url;
                    } else {
                        toastBody.innerHTML = '<i class="bi bi-exclamation-circle"></i> ' + data.message;
                        toast.querySelector('.toast').className = 'toast show align-items-center text-bg-warning border-0';
                        setTimeout(function() { toast.style.display = 'none'; }, 3000);
                    }
                }).catch(function() {
                    toastBody.innerHTML = '<i class="bi bi-x-circle"></i> Erreur de connexion';
                    setTimeout(function() { toast.style.display = 'none'; }, 3000);
                });
        }
    }

    // ── CSRF injection automatique ──
    var tokenMeta = document.querySelector('meta[name="csrf-token"]');
    if (tokenMeta) {
        var csrfValue = tokenMeta.getAttribute('content');
        document.querySelectorAll('form[method="post"], form[method="POST"]').forEach(function(form) {
            if (!form.querySelector('input[name="_csrf_token"]')) {
                var input = document.createElement('input');
                input.type = 'hidden';
                input.name = '_csrf_token';
                input.value = csrfValue;
                form.appendChild(input);
            }
        });
    }
})();
