from __future__ import annotations

"""Independent, read-only Shopify collection pagination audit for Duero.

An underfilled page is NOT proof of termination. Each pass continues until
the API returns an explicitly empty page. Two different page sizes must
enumerate exactly the same product/variant identities and commercial fields.

This verifies the public collections/products.json pagination, NOT a store,
postal code, unit package definition or a storefront product-count claim.
"""

import time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

PRIMARY_LIMIT = 50
CONTROL_LIMIT = 250


def _decimal(value) -> str | None:
    if value is None:
        return None
    try:
        return str(Decimal(str(value)).normalize())
    except (InvalidOperation, ValueError, TypeError):
        return None


def _identity(product: dict) -> tuple[str, dict[str, tuple], list[str]]:
    problems = []
    product_id = product.get("id")
    product_key = str(product_id) if product_id is not None else ""
    if not product_key:
        problems.append("Producto sin Shopify id")
        return "", {}, problems
    if not str(product.get("handle") or "").strip():
        problems.append("Producto sin handle: " + product_key)
    variants = product.get("variants")
    if not isinstance(variants, list) or not variants:
        problems.append("Producto sin variantes: " + product_key)
        return product_key, {}, problems

    snapshot = {}
    for variant in variants:
        if not isinstance(variant, dict):
            problems.append("Variante inválida en " + product_key)
            continue
        vid = variant.get("id")
        variant_key = str(vid) if vid is not None else ""
        if not variant_key:
            problems.append("Variante sin Shopify id en " + product_key)
            continue
        if variant_key in snapshot:
            problems.append("Variante duplicada dentro del producto: " + variant_key)
            continue
        snapshot[variant_key] = (
            product_key,
            str(variant.get("sku") or "").strip(),
            _decimal(variant.get("price")),
            _decimal(variant.get("compare_at_price")),
            variant.get("available") if isinstance(variant.get("available"), bool) else None,
        )
    return product_key, snapshot, problems


def _scan(request, *, category_id: str, base_url: str, limit: int,
          max_pages: int, delay_seconds: float) -> dict:
    products: dict[str, dict] = {}
    variants: dict[str, tuple] = {}
    page_log = []
    errors = []
    terminal_page = None

    for page_number in range(1, max_pages + 1):
        url = base_url + f"?limit={limit}&page={page_number}"
        started = time.monotonic()
        status = None
        batch = None
        try:
            response = request.get(url, timeout=60_000)
            status = response.status
            if status != 200:
                errors.append(f"limit={limit} page={page_number}: HTTP {status}")
                page_log.append({
                    "category_id": category_id, "limit": limit,
                    "page": page_number, "http_status": status,
                    "products": None, "new_products": 0,
                    "cumulative_products": len(products),
                    "cumulative_variants": len(variants),
                    "terminal_empty": False,
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "error": "HTTP " + str(status),
                })
                break
            payload = response.json()
            batch = payload.get("products") if isinstance(payload, dict) else None
            if not isinstance(batch, list):
                errors.append(f"limit={limit} page={page_number}: missing products array")
                page_log.append({
                    "category_id": category_id, "limit": limit,
                    "page": page_number, "http_status": status,
                    "products": None, "new_products": 0,
                    "cumulative_products": len(products),
                    "cumulative_variants": len(variants),
                    "terminal_empty": False,
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "error": "missing products array",
                })
                break
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            errors.append(f"limit={limit} page={page_number}: " + error)
            page_log.append({
                "category_id": category_id, "limit": limit,
                "page": page_number, "http_status": status,
                "products": None, "new_products": 0,
                "cumulative_products": len(products),
                "cumulative_variants": len(variants),
                "terminal_empty": False,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "error": error,
            })
            break

        if len(batch) > limit:
            errors.append(
                f"limit={limit} page={page_number}: devolvió {len(batch)} "
                "productos (límite ignorado)"
            )
            page_log.append({
                "category_id": category_id, "limit": limit,
                "page": page_number, "http_status": status,
                "products": len(batch), "new_products": 0,
                "cumulative_products": len(products),
                "cumulative_variants": len(variants),
                "terminal_empty": False,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "error": "limit ignored",
            })
            break

        page_ids = set()
        page_new = 0
        for item in batch:
            if not isinstance(item, dict):
                errors.append(f"limit={limit} page={page_number}: producto inválido")
                continue
            product_id, item_variants, problems = _identity(item)
            errors.extend(
                f"limit={limit} page={page_number}: {problem}"
                for problem in problems
            )
            if not product_id:
                continue
            if product_id in page_ids:
                errors.append(f"limit={limit} page={page_number}: ID repetido en página {product_id}")
                continue
            page_ids.add(product_id)
            if product_id in products:
                errors.append(
                    f"limit={limit} page={page_number}: producto repetido "
                    f"entre páginas {product_id}; paginación no demostrada"
                )
                continue
            products[product_id] = item
            page_new += 1
            for variant_id, signature in item_variants.items():
                if variant_id in variants:
                    errors.append(
                        f"limit={limit} page={page_number}: variante repetida "
                        + variant_id
                    )
                else:
                    variants[variant_id] = signature

        terminal = len(batch) == 0
        elapsed_ms = round((time.monotonic() - started) * 1000)
        page_log.append({
            "category_id": category_id,
            "limit": limit,
            "page": page_number,
            "http_status": status,
            "products": len(batch),
            "new_products": page_new,
            "cumulative_products": len(products),
            "cumulative_variants": len(variants),
            "terminal_empty": terminal,
            "elapsed_ms": elapsed_ms,
        })
        print(
            f"{category_id} | limit={limit} page={page_number} "
            f"products={len(batch)} new={page_new} "
            f"total={len(products)} variants={len(variants)} "
            f"terminal_empty={terminal}",
            flush=True,
        )

        if errors:
            break
        if terminal:
            terminal_page = page_number
            break

        # Lower API request pressure; do not fetch pages in parallel.
        if delay_seconds:
            time.sleep(delay_seconds)

    if not products:
        errors.append(f"limit={limit}: ningún producto de catálogo encontrado")
    if terminal_page is None:
        errors.append(f"limit={limit}: no se comprobó una página final vacía")
    return {
        "limit": limit,
        "products": products,
        "variants": variants,
        "pages": page_log,
        "terminal_page": terminal_page,
        "errors": errors,
    }


def audit_collection(request, category, *, max_pages: int = 30,
                     delay_seconds: float = 0.7) -> tuple[list[dict], dict, list[dict]]:
    """Return primary product objects, audit summary and per-request evidence."""
    if max_pages < 2:
        raise ValueError("max_pages debe ser >= 2 para verificar página terminal")
    if delay_seconds < 0:
        raise ValueError("delay_seconds debe ser >= 0")
    slug = urlsplit(category.url).path.rstrip("/").split("/")[-1]
    if not slug:
        raise ValueError("URL sin slug de colección")
    base = "https://dueromayoreo.mayoreoenlinea.mx/collections/" + slug + "/products.json"

    primary = _scan(
        request, category_id=category.id, base_url=base,
        limit=PRIMARY_LIMIT, max_pages=max_pages,
        delay_seconds=delay_seconds,
    )
    control = _scan(
        request, category_id=category.id, base_url=base,
        limit=CONTROL_LIMIT, max_pages=max_pages,
        delay_seconds=delay_seconds,
    )
    errors = primary["errors"] + control["errors"]

    a_ids = set(primary["products"])
    b_ids = set(control["products"])
    missing_primary = sorted(b_ids - a_ids)
    missing_control = sorted(a_ids - b_ids)
    if missing_primary or missing_control:
        errors.append(
            "Los recorridos no coinciden en productos: "
            f"faltan_limit50={len(missing_primary)}, "
            f"faltan_limit250={len(missing_control)}"
        )

    changed_products = [
        pid for pid in a_ids & b_ids
        if (
            str(primary["products"][pid].get("handle") or "").strip(),
            str(primary["products"][pid].get("title") or "").strip(),
            str(primary["products"][pid].get("vendor") or "").strip(),
        ) != (
            str(control["products"][pid].get("handle") or "").strip(),
            str(control["products"][pid].get("title") or "").strip(),
            str(control["products"][pid].get("vendor") or "").strip(),
        )
    ]
    if changed_products:
        errors.append(
            f"{len(changed_products)} productos cambiaron título/handle/marca "
            "entre pasadas"
        )

    a_variants = primary["variants"]
    b_variants = control["variants"]
    a_vkeys = set(a_variants)
    b_vkeys = set(b_variants)
    if a_vkeys != b_vkeys:
        errors.append(
            "Los recorridos no coinciden en variantes: "
            f"faltan_limit50={len(b_vkeys-a_vkeys)}, "
            f"faltan_limit250={len(a_vkeys-b_vkeys)}"
        )
    changed = [
        vid for vid in a_vkeys & b_vkeys
        if a_variants[vid] != b_variants[vid]
    ]
    if changed:
        errors.append(
            f"{len(changed)} variantes cambiaron SKU/precio/disponibilidad "
            "entre pasadas; repetir auditoría"
        )

    if not errors:
        # A positive but omitted/invalid price cannot simply disappear when
        # a variant is exported; the capture stage will also check its count.
        pagination_verified = True
    else:
        pagination_verified = False

    report = {
        "category_id": category.id,
        "pagination_complete": pagination_verified,
        "pagination_verified": pagination_verified,
        "independent_match": not bool(
            missing_primary or missing_control or changed_products
            or a_vkeys != b_vkeys or changed
        ),
        "first_pass_limit": PRIMARY_LIMIT,
        "second_pass_limit": CONTROL_LIMIT,
        "first_pass_pages": len(primary["pages"]),
        "second_pass_pages": len(control["pages"]),
        "terminal_page_limit50": primary["terminal_page"],
        "terminal_page_limit250": control["terminal_page"],
        "catalogue_products_seen": len(primary["products"]),
        "variant_count_api": len(primary["variants"]),
        "catalogue_count_source": "dual_shopify_api_not_storefront",
        "errors": errors,
        "missing_ids_limit50": missing_primary[:25],
        "missing_ids_limit250": missing_control[:25],
        "changed_variant_ids": changed[:25],
        "changed_product_ids": changed_products[:25],
    }
    return list(primary["products"].values()), report, primary["pages"] + control["pages"]
