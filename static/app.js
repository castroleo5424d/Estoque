// language: JavaScript, file: static/app.js
window.addEventListener("load", () => {
  // Auto-dismiss flash
  document.querySelectorAll(".flash").forEach(el => {
    setTimeout(() => { el.style.transition = "opacity .4s"; el.style.opacity = 0; }, 3500);
    setTimeout(() => el.remove(), 4000);
  });

  // Pega os elementos
  const sidebar = document.getElementById("sidebar");
  const hamburger = document.getElementById("hamburger");
  const overlay = document.getElementById("overlay");

  // Função para abrir/fechar
  function toggleMenu(e) {
    if (e) e.preventDefault(); // Previne que o clique faça scroll ou submit
    if (!sidebar || !overlay) return console.error("Sidebar ou overlay não encontrados");
    
    const isOpen = sidebar.classList.contains("open");
    if (isOpen) {
      sidebar.classList.remove("open");
      overlay.classList.remove("show");
    } else {
      sidebar.classList.add("open");
      overlay.classList.add("show");
    }
  }

  // Atrela os eventos
  if (hamburger) {
    hamburger.addEventListener("click", toggleMenu);
  }
  
  if (overlay) {
    overlay.addEventListener("click", toggleMenu);
  }
});