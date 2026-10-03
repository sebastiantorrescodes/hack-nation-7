export {};

const status = document.getElementById("status")!;

navigator.mediaDevices
  .getUserMedia({ audio: true })
  .then((stream) => {
    stream.getTracks().forEach((t) => t.stop());
    status.textContent = "Microphone allowed. You can close this tab.";
  })
  .catch((err) => {
    status.textContent = `Microphone blocked (${err.name}). Allow it from the camera/mic icon in the address bar, then reload.`;
  });
