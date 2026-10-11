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
        statusMsg.textContent = 'Menghubungi server dan mengunduh lagu... Ini mungkin butuh waktu agak lama untuk playlist.';
        statusMsg.className = 'status-msg status-loading';

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
                throw new Error(errorText);
            }

            const blob = await response.blob();

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
                filename += '.mp3';
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

            statusMsg.textContent = '✅ Download berhasil dimulai!';
            statusMsg.className = 'status-msg status-success';

        } catch (error) {
            console.error('Download error:', error);
            statusMsg.textContent = '❌ ' + error.message;
            statusMsg.className = 'status-msg status-error';
        } finally {
            submitBtn.disabled = false;
            btnText.textContent = 'Download Lagu';
            btnIcon.style.display = 'block';
            spinner.style.display = 'none';
        }
    });

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
