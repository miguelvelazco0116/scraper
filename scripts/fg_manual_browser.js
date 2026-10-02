(() => {
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const productRe = /-\d{5,14}\.html(?:$|[?#])/i;
  const moneyRe = /\$\s*[0-9][0-9,]*(?:\.\d{1,2})?/;

  const categoryFromUrl = () => {
    const p = location.pathname.toLowerCase();
    if (p.includes("cuidado-bucal")) return "cuidado-bucal";
    if (p.includes("lavanderia")) return "lavanderia";
    if (p.includes("preservativos")) return "preservativos";
    if (p.includes("vias-respiratorias")) return "vias-respiratorias";
    return "unknown";
  };

  const uniqueProductLinks = () => {
    const out = [];
    const seen = new Set();
    for (const a of Array.from(document.links)) {
      const href = a.href || "";
      if (!productRe.test(href) || seen.has(href)) continue;
      seen.add(href);
      out.push(a);
    }
    return out;
  };

  const targetProducts = () => {
    const text = document.body?.innerText || "";
    const matches = [...text.matchAll(/\(?\b(\d{1,5})\s+productos?\b\)?/gi)]
      .map(m => Number(m[1]))
      .filter(Number.isFinite);
    return matches.length ? Math.max(...matches) : null;
  };

  const findMoreButton = () => {
    const candidates = Array.from(document.getElementsByTagName("button"));
    return candidates.find(b =>
      b.classList.contains("more") &&
      b.getAttribute("data-url") &&
      /ver\s+m[aá]s\s+productos/i.test((b.textContent || "").trim())
    ) || null;
  };

  const extractCards = () => {
    const out = [];
    const seen = new Set();

    for (const a of uniqueProductLinks()) {
      const href = a.href || "";
      if (seen.has(href)) continue;

      let node = a;
      let card = null;
      for (let i = 0; i < 9 && node; i++, node = node.parentElement) {
        const text = (node.innerText || "").trim();
        if (moneyRe.test(text) && text.length >= 15 && text.length <= 3000) {
          card = node;
          if (/Agregar|Comparar|Favoritos/i.test(text)) break;
        }
      }

      const source = card || a.parentElement || a;
      const text = (source.innerText || "").trim();
      const named = source.querySelector('[class*="brand" i]');
      const titleNode = source.querySelector(
        'h2, h3, h4, [class*="name" i], [class*="title" i]'
      );

      let name = (
        a.innerText ||
        a.getAttribute("aria-label") ||
        a.getAttribute("title") ||
        ""
      ).trim();

      if (!name && titleNode) {
        name = (titleNode.innerText || "").trim();
      }

      if (!name) {
        const lines = text.split(/\n+/).map(x => x.trim()).filter(Boolean);
        name = lines.find(x =>
          !moneyRe.test(x) &&
          !/Agregar|Comparar|Oferta|Favoritos/i.test(x) &&
          x.length > 8
        ) || "";
      }

      const dataPid =
        source.getAttribute("data-product-id") ||
        source.getAttribute("data-part-number") ||
        a.getAttribute("data-product-id") ||
        "";

      seen.add(href);
      out.push({
        href,
        name,
        brand: named ? (named.innerText || "").trim() : "",
        text,
        dataPid
      });
    }

    return out;
  };

  const downloadJson = payload => {
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const filename =
      `farmacias_guadalajara_${payload.category_id}_${stamp}.json`;
    const blob = new Blob(
      [JSON.stringify(payload, null, 2)],
      { type: "application/json;charset=utf-8" }
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    return filename;
  };

  (async () => {
    const categoryId = categoryFromUrl();
    const target = targetProducts();
    let previous = uniqueProductLinks().length;
    const progress = [];

    console.log(
      `FG manual extractor: category=${categoryId}, target=${target}, initial=${previous}`
    );

    for (let step = 1; step <= 100; step++) {
      if (target && previous >= target) break;

      const button = findMoreButton();
      if (!button) {
        progress.push({
          step,
          before: previous,
          after: previous,
          status: "button_not_found"
        });
        break;
      }

      const dataUrl = button.getAttribute("data-url");
      button.scrollIntoView({ behavior: "auto", block: "center" });
      await sleep(400);
      button.click();

      let current = previous;
      for (let wait = 0; wait < 30; wait++) {
        await sleep(500);
        current = uniqueProductLinks().length;
        if (current > previous) break;
      }

      progress.push({
        step,
        before: previous,
        after: current,
        data_url: dataUrl,
        status: current > previous ? "grown" : "no_growth"
      });

      console.log(`FG: ${previous} -> ${current}`);

      if (current <= previous) break;
      previous = current;
      await sleep(1200);
    }

    const cards = extractCards();
    const finalLinks = uniqueProductLinks().length;
    const payload = {
      retailer: "Farmacias Guadalajara",
      category_id: categoryId,
      page_url: location.href,
      scraped_at: new Date().toISOString(),
      target_products: target,
      product_links: finalLinks,
      cards,
      progress
    };

    console.log({
      category_id: categoryId,
      target_products: target,
      product_links: finalLinks,
      cards: cards.length
    });

    if (target && finalLinks < target) {
      console.warn(
        `FG extracción incompleta: ${finalLinks}/${target}. Se descargará el diagnóstico para revisión.`
      );
    }

    const filename = downloadJson(payload);
    console.log(`FG JSON descargado: ${filename}`);
  })().catch(err => {
    console.error("FG manual extractor ERROR", err);
  });
})();