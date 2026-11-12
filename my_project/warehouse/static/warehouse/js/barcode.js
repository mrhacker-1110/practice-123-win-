document.addEventListener('DOMContentLoaded', () => {
  const button = document.querySelector('#barcode-scan-button');
  const skuInput = document.querySelector('#id_sku');
  const status = document.querySelector('#barcode-scan-status');
  if (!button || !skuInput || !status) return;
  const russian = document.documentElement.lang === 'ru';
  let stream = null;
  let frame = 0;
  let detector = null;
  const stopCamera = () => {
    cancelAnimationFrame(frame);
    if (stream) stream.getTracks().forEach((track) => track.stop());
    stream = null;
  };
  const dialog = document.createElement('dialog');
  dialog.className = 'barcode-dialog';
  const title = document.createElement('h2');
  title.textContent = russian ? 'Сканирование штрихкода' : 'Scan a barcode';
  const video = document.createElement('video');
  video.autoplay = true;
  video.muted = true;
  video.playsInline = true;
  video.setAttribute('aria-label', russian ? 'Изображение с камеры' : 'Camera preview');
  const note = document.createElement('p');
  note.className = 'help-text';
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'btn btn-secondary';
  close.textContent = russian ? 'Закрыть' : 'Close';
  dialog.append(title, video, note, close);
  document.body.appendChild(dialog);
  const closeScanner = () => { stopCamera(); if (dialog.open) dialog.close(); };
  close.addEventListener('click', closeScanner);
  dialog.addEventListener('cancel', stopCamera);
  dialog.addEventListener('close', stopCamera);
  const scanFrame = async () => {
    if (!dialog.open || !detector || video.readyState < 2) { frame = requestAnimationFrame(scanFrame); return; }
    try {
      const codes = await detector.detect(video);
      if (codes.length && codes[0].rawValue) {
        skuInput.value = codes[0].rawValue.trim();
        skuInput.dispatchEvent(new Event('input', { bubbles: true }));
        status.textContent = russian ? 'Штрихкод добавлен в поле артикула.' : 'Barcode added to the SKU field.';
        closeScanner();
        skuInput.focus();
        return;
      }
    } catch (_) { /* Keep the camera preview active while the next frame is checked. */ }
    frame = requestAnimationFrame(scanFrame);
  };
  button.addEventListener('click', async () => {
    status.textContent = '';
    if (!('BarcodeDetector' in window) || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      status.textContent = russian ? 'Сканирование не поддерживается в этом браузере. Введите артикул вручную.' : 'Scanning is not supported in this browser. Enter the SKU manually.';
      return;
    }
    try {
      detector = new BarcodeDetector({ formats: ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'qr_code'] });
      dialog.showModal();
      note.textContent = russian ? 'Наведите камеру на штрихкод. Камера используется только во время сканирования.' : 'Point the camera at a barcode. The camera is used only while scanning.';
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
      video.srcObject = stream;
      await video.play();
      frame = requestAnimationFrame(scanFrame);
    } catch (_) {
      closeScanner();
      status.textContent = russian ? 'Не удалось открыть камеру. Проверьте разрешение браузера и HTTPS.' : 'Could not open the camera. Check browser permission and HTTPS.';
    }
  });
});