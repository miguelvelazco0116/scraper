param(
    [ValidateSet("candidates", "all", "higiene-bucal", "lavanderia", "desodorantes-y-antitranspirantes", "depilacion", "respiratorios")]
    [string]$Category = "candidates",
    [ValidateRange(0, 600)]
    [int]$StoreDelaySeconds = 45,
    [switch]$Headed,
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

$stores = @(
    "chedraui-polanco",
    "chedraui-tepeyac",
    "chedraui-cancun-kabah",
    "chedraui-puebla-centro-sur"
)

Write-Host "========================================================================"
Write-Host "CHEDRAUI - TEST MULTITIENDA SECUENCIAL"
Write-Host "========================================================================"
Write-Host "Tiendas      : $($stores.Count)"
Write-Host "Categoria(s) : $Category"
Write-Host "Delay tienda : $StoreDelaySeconds segundos"
Write-Host "Modo         : SECUENCIAL (sin paralelismo)"
Write-Host "Consolidado  : sin cambios"
Write-Host ""

$results = @()
$overallExit = 0

for ($i = 0; $i -lt $stores.Count; $i++) {
    $store = $stores[$i]

    Write-Host ""
    Write-Host "========================================================================"
    Write-Host "TIENDA $($i + 1)/$($stores.Count): $store"
    Write-Host "========================================================================"

    $invokeParams = @{
        Store = $store
        Category = $Category
    }

    if ($Headed) {
        $invokeParams["Headed"] = $true
    }

    if (-not [string]::IsNullOrWhiteSpace($PythonExe)) {
        $invokeParams["PythonExe"] = $PythonExe
    }

    & ".\scripts\test_chedraui.ps1" @invokeParams
    $code = $LASTEXITCODE

    $results += [PSCustomObject]@{
        Store = $store
        ExitCode = $code
        Status = if ($code -eq 0) { "SUCCESS" } else { "OBSERVATIONS" }
    }

    if ($code -ne 0) {
        $overallExit = 2
    }

    if ($i -lt ($stores.Count - 1) -and $StoreDelaySeconds -gt 0) {
        Write-Host ""
        Write-Host "Pausa de $StoreDelaySeconds segundos antes de la siguiente tienda..."
        Start-Sleep -Seconds $StoreDelaySeconds
    }
}

Write-Host ""
Write-Host "========================================================================"
Write-Host "RESUMEN TEST MULTITIENDA CHEDRAUI"
Write-Host "========================================================================"
$results | Format-Table -AutoSize
Write-Host ""
Write-Host "No se modifico el concentrado productivo."

exit $overallExit
