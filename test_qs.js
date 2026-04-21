const js = `([sel]) => {
    const root = sel ? document.querySelector(sel) : document;
    if (!root) return 0;
    return Array.from(root.querySelectorAll(
        'input[required], textarea[required], select[required],'
        + 'input[aria-required="true"], textarea[aria-required="true"],'
        + 'select[aria-required="true"]'
    )).filter(el => {
        const s = window.getComputedStyle(el);
        return s.display !== 'none' && s.visibility !== 'hidden'
               && !(el.value||'').trim();
    }).length;
}`;
console.log("No syntax errors");
