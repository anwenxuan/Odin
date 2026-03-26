/* ============================================
   Odin Docs — Navigation & Interaction
   ============================================ */

(function () {
  "use strict";

  /* ---------- Scroll → nav shadow ---------- */
  function initNavScroll() {
    const nav = document.querySelector(".nav");
    if (!nav) return;

    let ticking = false;

    function updateNav() {
      if (window.scrollY > 10) {
        nav.classList.add("scrolled");
      } else {
        nav.classList.remove("scrolled");
      }
      ticking = false;
    }

    window.addEventListener("scroll", function () {
      if (!ticking) {
        requestAnimationFrame(updateNav);
        ticking = true;
      }
    }, { passive: true });

    updateNav();
  }

  /* ---------- Active nav link ---------- */
  function initActiveNavLink() {
    const navLinks = document.querySelectorAll(".nav__link");
    if (!navLinks.length) return;

    const currentPath = window.location.pathname;
    const currentHref = window.location.href;

    navLinks.forEach(function (link) {
      const href = link.getAttribute("href");
      if (!href) return;

      // Exact match on index
      if (href === "index.html" || href === "/" || href.endsWith("/index.html")) {
        if (currentPath === "/" || currentPath.endsWith("index.html") || currentPath === "") {
          link.classList.add("active");
        }
        return;
      }

      // Prefix match so sub-pages highlight parent
      if (currentHref.includes(href) && href !== "#") {
        link.classList.add("active");
      }
    });
  }

  /* ---------- Mobile hamburger ---------- */
  function initHamburger() {
    const hamburger = document.querySelector(".nav__hamburger");
    const nav = document.querySelector(".nav");

    if (!hamburger || !nav) return;

    hamburger.addEventListener("click", function () {
      const isOpen = nav.classList.toggle("mobile-open");
      hamburger.classList.toggle("active", !isOpen);
      document.body.style.overflow = isOpen ? "" : "hidden";
    });

    // Close on outside click
    document.addEventListener("click", function (e) {
      if (!nav.contains(e.target) && nav.classList.contains("mobile-open")) {
        nav.classList.remove("mobile-open");
        hamburger.classList.remove("active");
        document.body.style.overflow = "";
      }
    });

    // Close on escape
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && nav.classList.contains("mobile-open")) {
        nav.classList.remove("mobile-open");
        hamburger.classList.remove("active");
        document.body.style.overflow = "";
      }
    });
  }

  /* ---------- Code block copy ---------- */
  function initCopyButtons() {
    const copyButtons = document.querySelectorAll(".code-block__copy");

    copyButtons.forEach(function (btn) {
      btn.addEventListener("click", async function () {
        const block = btn.closest(".code-block");
        if (!block) return;

        const pre = block.querySelector("pre");
        if (!pre) return;

        const text = pre.textContent || "";

        try {
          await navigator.clipboard.writeText(text);

          const originalHTML = btn.innerHTML;
          btn.classList.add("copied");
          btn.innerHTML =
            '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg> Copied';

          setTimeout(function () {
            btn.classList.remove("copied");
            btn.innerHTML = originalHTML;
          }, 2000);
        } catch (err) {
          // Fallback: select text
          const range = document.createRange();
          range.selectNode(pre);
          window.getSelection().removeAllRanges();
          window.getSelection().addRange(range);
          document.execCommand("copy");
          window.getSelection().removeAllRanges();
        }
      });
    });
  }

  /* ---------- Smooth scroll for anchor links ---------- */
  function initSmoothScroll() {
    document.querySelectorAll('a[href^="#"]').forEach(function (anchor) {
      anchor.addEventListener("click", function (e) {
        const targetId = this.getAttribute("href").slice(1);
        if (!targetId) return;

        const target = document.getElementById(targetId);
        if (!target) return;

        e.preventDefault();
        const navHeight = document.querySelector(".nav")?.offsetHeight || 56;
        const top = target.getBoundingClientRect().top + window.scrollY - navHeight - 20;

        window.scrollTo({ top: top, behavior: "smooth" });

        // Update URL without jump
        history.pushState(null, "", "#" + targetId);
      });
    });
  }

  /* ---------- TOC active link on scroll ---------- */
  function initTOC() {
    const tocLinks = document.querySelectorAll(".toc__link");
    if (!tocLinks.length) return;

    const headings = [];
    tocLinks.forEach(function (link) {
      const id = link.getAttribute("href");
      if (!id || !id.startsWith("#")) return;
      const el = document.querySelector(id);
      if (el) headings.push({ el, link });
    });

    if (!headings.length) return;

    let current = "";

    function update() {
      const navHeight = document.querySelector(".nav")?.offsetHeight || 56;
      const scrollY = window.scrollY + navHeight + 80;

      for (let i = headings.length - 1; i >= 0; i--) {
        if (scrollY >= headings[i].el.offsetTop) {
          current = headings[i].link.getAttribute("href");
          break;
        }
      }

      tocLinks.forEach(function (link) {
        link.classList.toggle("active", link.getAttribute("href") === current);
      });
    }

    window.addEventListener("scroll", update, { passive: true });
    update();
  }

  /* ---------- Init all ---------- */
  function init() {
    initNavScroll();
    initActiveNavLink();
    initHamburger();
    initCopyButtons();
    initSmoothScroll();
    initTOC();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
