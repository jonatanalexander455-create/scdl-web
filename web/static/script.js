document.addEventListener('DOMContentLoaded', () => {
    const dlForm = document.getElementById('downloadForm');
    const submitBtn = document.getElementById('downloadBtn');
    const btnText = submitBtn.querySelector('.btn-text');
    const btnIcon = submitBtn.querySelector('.btn-icon');
    const spinner = submitBtn.querySelector('.spinner');
    const statusMsg = document.getElementById('statusMessage');
    const saveTokenBtn = document.getElementById('saveTokenBtn');

    if (saveTokenBtn) {
        saveTokenBtn.addEventListener('click', async () => {
            const authToken = document.getElementById('auth_token').value.trim();
            if (!authToken) {
                statusMsg.textContent = 'Token masih kosong. Paste token dulu sebelum disimpan.';
                statusMsg.className = 'status-msg status-error';
                return;
            }

            try {
                const response = await fetch('/api/save_token', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ auth_token: authToken })
                });

                const data = await response.json();
                if (data.success) {
                    statusMsg.textContent = '✅ ' + data.message;
                    statusMsg.className = 'status-msg status-success';
                } else {
                    statusMsg.textContent = '❌ Gagal: ' + data.error;
                    statusMsg.className = 'status-msg status-error';
                }
            } catch (error) {
                statusMsg.textContent = '❌ Error menghubungi server.';
                statusMsg.className = 'status-msg status-error';
            }
        });
    }

    const progressContainer = document.getElementById('progressContainer');
    const progressBar = document.getElementById('progressBar');
    const progressPercent = document.getElementById('progressPercent');
    const progressStatus = document.getElementById('progressStatus');

    let progressTimer = null;

    function setProgress(percent, text, state = 'normal') {
        if (!progressContainer) return;
        progressContainer.style.display = 'block';
        if (progressBar) {
            progressBar.style.width = Math.min(100, Math.max(0, percent)) + '%';
            progressBar.className = 'progress-bar' + (state === 'success' ? ' success' : state === 'error' ? ' error' : '');
        }
        if (progressPercent) {
            progressPercent.textContent = Math.round(percent) + '%';
        }
        if (progressStatus && text) {
            progressStatus.textContent = text;
        }
    }

    dlForm.addEventListener('submit', async (e) => {
        e.preventDefault();

        const url = document.getElementById('url').value;
        const authTokenEl = document.getElementById('auth_token');
        const authToken = authTokenEl ? authTokenEl.value.trim() : '';
        const format = document.getElementById('format').value;

        if (!url) return;

        // Loading state
        submitBtn.disabled = true;
        btnText.textContent = 'Mendownload...';
        btnIcon.style.display = 'none';
        spinner.style.display = 'block';
        statusMsg.textContent = '';
        statusMsg.className = 'status-msg';

        if (progressTimer) clearInterval(progressTimer);
        let currentPercent = 10;
        setProgress(currentPercent, '🔍 Memeriksa link & menghubungkan ke SoundCloud...');

        progressTimer = setInterval(() => {
            if (currentPercent < 30) {
                currentPercent += 5;
                setProgress(currentPercent, '🔍 Autentikasi Master Go+ & mencari stream...');
            } else if (currentPercent < 65) {
                currentPercent += 4;
                setProgress(currentPercent, '🎵 Mengunduh stream audio SoundCloud HQ...');
            } else if (currentPercent < 88) {
                currentPercent += 2;
                setProgress(currentPercent, '🎛️ Memproses format audio & cover art...');
            } else if (currentPercent < 95) {
                currentPercent += 0.5;
                setProgress(currentPercent, '📦 Menyiapkan file untuk disimpan...');
            }
        }, 300);

        try {
            const response = await fetch('/api/download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    url: url,
                    auth_token: authToken,
                    format: format
                })
            });

            if (!response.ok) {
                let errorText = 'Terjadi kesalahan sistem';
                try {
                    const errorData = await response.json();
                    errorText = errorData.error || errorText;
                } catch {
                    errorText = `HTTP Error: ${response.status}`;
                }
                if (response.status === 401 || response.status === 403) {
                    setTimeout(() => {
                        window.location.href = '/login';
                    }, 2800);
                }
                throw new Error(errorText);
            }

            const blob = await response.blob();

            clearInterval(progressTimer);
            setProgress(100, '✅ Download selesai! Menyimpan file...', 'success');

            let filename = 'soundcloud_download';
            const disposition = response.headers.get('Content-Disposition');
            if (disposition && disposition.indexOf('attachment') !== -1) {
                const filenameRegex = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/;
                const matches = filenameRegex.exec(disposition);
                if (matches != null && matches[1]) {
                    filename = matches[1].replace(/['"]/g, '');
                }
            } else if (response.headers.get('Content-Type') === 'application/zip') {
                filename += '.zip';
            } else {
                filename += '.' + (format || 'mp3');
            }

            const downloadUrl = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = downloadUrl;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(downloadUrl);
            document.body.removeChild(a);

            statusMsg.textContent = '✅ ' + filename + ' berhasil diunduh!';
            statusMsg.className = 'status-msg status-success';

        } catch (error) {
            console.error('Download error:', error);
            if (progressTimer) clearInterval(progressTimer);
            setProgress(currentPercent, '❌ Gagal mengunduh lagu', 'error');
            statusMsg.textContent = '❌ ' + error.message;
            statusMsg.className = 'status-msg status-error';
        } finally {
            submitBtn.disabled = false;
            btnText.textContent = 'Download Lagu';
            btnIcon.style.display = 'block';
            spinner.style.display = 'none';
        }
    });

    // Live Track Preview Fetching
    const urlInput = document.getElementById('url');
    const trackPreview = document.getElementById('trackPreview');
    const previewArtwork = document.getElementById('previewArtwork');
    const previewTitle = document.getElementById('previewTitle');
    const previewArtist = document.getElementById('previewArtist');
    const previewBadge = document.getElementById('previewBadge');
    const previewPlayerWrap = document.getElementById('previewPlayerWrap');
    let previewDebounce = null;
    let lastPreviewUrl = '';

    async function fetchTrackPreview(rawUrl) {
        const trimmed = rawUrl.trim();
        if (!trimmed || !trimmed.includes('soundcloud.com')) {
            if (trackPreview) trackPreview.style.display = 'none';
            if (previewPlayerWrap) previewPlayerWrap.innerHTML = '';
            lastPreviewUrl = '';
            return;
        }

        if (trimmed === lastPreviewUrl) return;
        lastPreviewUrl = trimmed;

        try {
            const res = await fetch('/api/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: trimmed })
            });
            if (!res.ok) {
                if (trackPreview) trackPreview.style.display = 'none';
                return;
            }
            const data = await res.json();
            if (data.success) {
                if (previewTitle) previewTitle.textContent = data.title || 'SoundCloud Track';
                if (previewArtist) previewArtist.textContent = data.artist || 'Unknown Artist';
                if (previewBadge) previewBadge.textContent = data.is_playlist ? 'Playlist' : 'Lagu';
                if (previewArtwork) {
                    if (data.artwork_url) {
                        previewArtwork.src = data.artwork_url;
                        previewArtwork.style.display = 'block';
                    } else {
                        previewArtwork.style.display = 'none';
                    }
                }
                if (previewPlayerWrap && data.widget_url) {
                    previewPlayerWrap.innerHTML = `<iframe scrolling="no" frameborder="no" allow="autoplay" src="${data.widget_url}"></iframe>`;
                }
                if (trackPreview) trackPreview.style.display = 'block';
            } else {
                if (trackPreview) trackPreview.style.display = 'none';
            }
        } catch (err) {
            console.warn('Preview error:', err);
            if (trackPreview) trackPreview.style.display = 'none';
        }
    }

    if (urlInput) {
        urlInput.addEventListener('input', (e) => {
            clearTimeout(previewDebounce);
            previewDebounce = setTimeout(() => {
                fetchTrackPreview(e.target.value);
            }, 450);
        });

        urlInput.addEventListener('paste', (e) => {
            setTimeout(() => {
                fetchTrackPreview(urlInput.value);
            }, 50);
        });
    }

    // Subtle focus animation
    document.querySelectorAll('.field input').forEach(input => {
        input.addEventListener('focus', () => {
            input.parentElement.style.transform = 'translateY(-1px)';
            input.parentElement.style.transition = 'transform 0.2s ease';
        });
        input.addEventListener('blur', () => {
            input.parentElement.style.transform = 'translateY(0)';
        });
    });
});
