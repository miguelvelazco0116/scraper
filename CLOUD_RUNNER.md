# Solución cloud para Walmart + Soriana

Esta arquitectura evita usar una computadora personal como runner. El scraper se ejecuta en una VM Linux dedicada en Azure y mantiene un perfil persistente de Chromium para Walmart / SC Toreo.

## Arquitectura

```text
GitHub Actions (repo privado)
        |
        v
Azure VM Linux dedicada
  - GitHub self-hosted runner
  - etiqueta: cloud-scraper
  - disco persistente
  - Xvfb para Chromium headed
  - perfil Walmart persistente
        |
        +--> Soriana: todas las categorías
        |
        +--> Walmart / SC Toreo: todas las categorías
        |
        v
output/concentrado_scraper.xlsx
```

La VM no necesita una IP pública de entrada si se administra mediante Azure Bastion. Para conservar una salida estable a internet, se recomienda una IP de egreso explícita y fija, por ejemplo mediante Azure NAT Gateway con una Public IP estática asociada al subnet de la VM.

## 1. Repositorio privado

Antes de registrar el runner cloud, cambia `miguelvelazco0116/scraper` a privado desde GitHub:

`Settings -> General -> Danger Zone -> Change repository visibility -> Make private`.

## 2. Preparar la VM

Recomendación operativa: Ubuntu 22.04/24.04 con disco persistente y recursos suficientes para Chromium.

Clona el repositorio y ejecuta:

```bash
chmod +x scripts/setup_cloud_runner_ubuntu.sh
./scripts/setup_cloud_runner_ubuntu.sh
```

El script instala:

- Python y venv
- Chromium/Playwright
- Xvfb
- XFCE
- xrdp
- dependencias del proyecto

Perfil Walmart predeterminado:

```text
$HOME/scraper_profiles/walmart_sc_toreo
```

## 3. Registrar la VM como runner

En GitHub:

`Settings -> Actions -> Runners -> New self-hosted runner -> Linux -> x64`.

Ejecuta en la VM los comandos generados por GitHub y agrega la etiqueta adicional:

```text
cloud-scraper
```

El runner puede funcionar como servicio porque las ejecuciones automáticas usan `xvfb-run`.

## 4. Preparar SC Toreo una sola vez

Conéctate a la VM mediante Azure Bastion usando RDP. La VM usa `xrdp`, por lo que tendrás un escritorio Linux remoto sin exponer RDP a internet.

Desde una terminal dentro del escritorio ejecuta:

```bash
chmod +x scripts/setup_walmart_session_linux.sh
./scripts/setup_walmart_session_linux.sh
```

Se abrirá Walmart en Chromium con el perfil persistente. Completa manualmente cualquier verificación solicitada y confirma:

```text
Tienda: SC Toreo
Store ID: 2344
CP: 11220
```

Después cierra la sesión de preparación. Las futuras corridas reutilizan el mismo perfil. No se automatiza ni evade ninguna verificación de identidad.

## 5. Variable opcional de GitHub

Si quieres usar otra ruta de perfil, crea una Repository Variable:

```text
WALMART_PROFILE_DIR=/home/<usuario>/scraper_profiles/walmart_sc_toreo
```

Si no existe, el workflow usa `$HOME/scraper_profiles/walmart_sc_toreo`.

## 6. Ejecutar

En GitHub Actions selecciona:

```text
Full Scraper - Cloud VM
```

y usa `Run workflow`.

La misma VM ejecuta todas las categorías habilitadas de Soriana y Walmart y publica un único artifact:

```text
output/concentrado_scraper.xlsx
```

Hojas:

- `Concentrado`
- `Resumen`

Walmart sólo se considera válido si el scraper verifica el contexto de SC Toreo. Si vuelve a aparecer el desafío de identidad, el caso queda `BLOCKED`; vuelve a conectarte por Bastion/RDP y ejecuta `setup_walmart_session_linux.sh` para renovar la sesión.

## Nota sobre IP de salida

Una VM cloud puede seguir recibiendo desafíos de Walmart. La combinación de perfil persistente + disco persistente + una IP de egreso estable reduce cambios de sesión y hace la operación reproducible, pero no garantiza que Walmart nunca vuelva a pedir verificación. El scraper debe seguir deteniéndose ante cualquier desafío y nunca intentar resolverlo automáticamente.
