/* ─── Odin Docs — Shared Scripts ─── */

// ─── Hamburger Menu ───
document.addEventListener('DOMContentLoaded', function () {
  const hamburger = document.getElementById('hamburger');
  const sidebar = document.getElementById('sidebar');

  if (hamburger && sidebar) {
    hamburger.addEventListener('click', function () {
      sidebar.classList.toggle('open');
    });

    // Close sidebar when clicking outside on mobile
    document.addEventListener('click', function (e) {
      if (sidebar.classList.contains('open') &&
          !sidebar.contains(e.target) &&
          !hamburger.contains(e.target)) {
        sidebar.classList.remove('open');
      }
    });
  }

  // ─── Active Sidebar Link ───
  const currentPage = window.location.pathname.split('/').pop() || 'index.html';
  const links = document.querySelectorAll('.sidebar-link');
  links.forEach(function (link) {
    const href = link.getAttribute('href');
    if (href === currentPage) {
      link.classList.add('active');
    } else {
      link.classList.remove('active');
    }
  });

  // ─── Keyboard Shortcuts ───
  document.addEventListener('keydown', function (e) {
    // Cmd/Ctrl + K → focus search
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      document.getElementById('searchModal').style.display = 'flex';
    }
    // Escape → close modals
    if (e.key === 'Escape') {
      document.getElementById('searchModal').style.display = 'none';
      if (sidebar && sidebar.classList.contains('open')) {
        sidebar.classList.remove('open');
      }
    }
  });

  // ─── Intersection Observer — fade-in animations ───
  const observer = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('animate-in');
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.1 }
  );

  document.querySelectorAll('h2, .card, .stat-card, .arch-layer').forEach(function (el) {
    el.style.opacity = '0';
    observer.observe(el);
  });

  // Fix: hero elements should always be visible
  document.querySelectorAll('.docs-hero').forEach(function (el) {
    el.style.opacity = '1';
  });

  // ─── Copy Code Blocks ───
  document.querySelectorAll('pre').forEach(function (pre) {
    const wrapper = document.createElement('div');
    wrapper.style.position = 'relative';
    pre.parentNode.insertBefore(wrapper, pre);
    wrapper.appendChild(pre);

    const copyBtn = document.createElement('button');
    copyBtn.textContent = 'Copy';
    copyBtn.style.cssText = 'position:absolute; top:10px; right:10px; background:var(--bg-elevated); border:1px solid var(--border-bright); border-radius:6px; padding:4px 10px; font-size:11px; font-weight:600; color:var(--text-muted); cursor:pointer; font-family:var(--font-sans); transition:all 0.2s;';
    copyBtn.addEventListener('mouseenter', function () {
      copyBtn.style.color = 'var(--accent-cyan)';
      copyBtn.style.borderColor = 'var(--accent-cyan)';
    });
    copyBtn.addEventListener('mouseleave', function () {
      copyBtn.style.color = 'var(--text-muted)';
      copyBtn.style.borderColor = 'var(--border-bright)';
    });
    copyBtn.addEventListener('click', function () {
      const code = pre.querySelector('code');
      if (code) {
        navigator.clipboard.writeText(code.textContent || '').then(function () {
          copyBtn.textContent = 'Copied!';
          copyBtn.style.color = 'var(--accent-green)';
          copyBtn.style.borderColor = 'var(--accent-green)';
          setTimeout(function () {
            copyBtn.textContent = 'Copy';
            copyBtn.style.color = 'var(--text-muted)';
            copyBtn.style.borderColor = 'var(--border-bright)';
          }, 2000);
        });
      }
    });
    wrapper.appendChild(copyBtn);
  });
});
