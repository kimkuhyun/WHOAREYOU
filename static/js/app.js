window.toast = function (icon, title) {
  Swal.fire({
    toast: true,
    position: 'top-end',
    icon: icon || 'success',
    title: title || '',
    showConfirmButton: false,
    timer: 2200,
    timerProgressBar: true,
  });
};

window.confirmDanger = async function (title, text) {
  const result = await Swal.fire({
    title: title || '확인',
    text: text || '',
    icon: 'warning',
    showCancelButton: true,
    confirmButtonText: '실행',
    cancelButtonText: '취소',
    confirmButtonColor: '#dc2626',
  });
  return result.isConfirmed;
};

document.body.addEventListener('htmx:responseError', (evt) => {
  window.toast('error', `요청 실패 (${evt.detail.xhr.status})`);
});
